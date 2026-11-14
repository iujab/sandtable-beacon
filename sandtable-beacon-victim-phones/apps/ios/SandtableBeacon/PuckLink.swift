// SandtableBeacon - PuckLink.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// One PuckLink = one connected rescue board (Qorvo DWM3001CDK running Qorvo's stock
// Nearby Interaction firmware) with its own NISession.
//
// The Core Bluetooth and Nearby Interaction call sequence is modelled on Apple's public
// sample "Implementing spatial interactions with third-party accessories"
// (BluetoothSupport/BluetoothLECentral.swift and AccessoryDemoViewController.swift).
// This is our own code written after reading the sample, not a copy of it. Where we do
// something different from the sample, a comment starting "Differs from the sample"
// says what and why.
//
// Protocol, per Apple's sample (one message-id byte, then the payload):
//   board -> phone: 0x01 accessory configuration data, 0x02 UWB started, 0x03 UWB stopped
//   phone -> board: 0x0A initialize, 0x0B configure and start (+ shareable config), 0x0C stop
// That Qorvo's stock firmware uses exactly these ids is consistent with forum reports
// but was not confirmed against Qorvo source (docs/research/ios_ni.md, section B).
//
// Threading: every callback here arrives on the main queue (the CBCentralManager is
// created with queue nil, and NISession calls its delegate on main by default), so
// no locking and no @MainActor annotations are used.

import Foundation
import CoreBluetooth
import NearbyInteraction
import simd

// MARK: - UUIDs and message ids

enum PuckUUIDs {
    // Qorvo's current Nearby Interaction firmware ("QorvoNIService"), from the Qorvo forum
    // (docs/research/ios_ni.md, section 7). rx = the phone writes, tx = the board notifies.
    static let qorvoService = CBUUID(string: "2E938FD0-6A61-11ED-A1EB-0242AC120002")
    static let qorvoRx = CBUUID(string: "2E93998A-6A61-11ED-A1EB-0242AC120002")
    static let qorvoTx = CBUUID(string: "2E939AF2-6A61-11ED-A1EB-0242AC120002")

    // Nordic UART UUIDs used by Apple's sample (and by older Qorvo firmware).
    static let nordicService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
    static let nordicRx = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
    static let nordicTx = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

    static let services: [CBUUID] = [qorvoService, nordicService]
    static let rxAll: [CBUUID] = [qorvoRx, nordicRx]
    static let txAll: [CBUUID] = [qorvoTx, nordicTx]
}

/// Same values as `enum MessageId` in Apple's sample.
enum MessageId: UInt8 {
    // Messages from the accessory.
    case accessoryConfigurationData = 0x01
    case accessoryUwbDidStart = 0x02
    case accessoryUwbDidStop = 0x03

    // Messages to the accessory.
    case initialize = 0x0A
    case configureAndStart = 0x0B
    case stop = 0x0C
}

enum LinkPhase: String {
    case connecting = "Connecting"
    case discovering = "Finding service"
    case subscribing = "Subscribing"
    case waitingForConfig = "Asking board to start"
    case startingSession = "Starting UWB"
    case ranging = "Ranging"
    case suspended = "Paused"
    case accessoryStopped = "Board stopped"
    case bluetoothOnly = "Bluetooth only"
    case failed = "Failed"
}

/// What the reporter needs from one board, computed on demand.
struct PuckReading {
    let key: String
    let distance: Double?
    let distanceTime: TimeInterval
    let rssi: Double?
    let rssiTime: TimeInterval
    /// Azimuth from the phone to the board, degrees, positive = to the right of where
    /// the back camera points. Nil when the phone has no fresh direction estimate.
    let azimuthDeg: Double?
    let azimuthSource: String?
}

// MARK: - PuckLink

final class PuckLink: NSObject {
    let peripheral: CBPeripheral
    let advertisedName: String
    let nameOnlyKey: Bool
    let wantsCameraAssist: Bool
    let uwbSupported: Bool
    let connectStartedAt: TimeInterval

    private(set) var connectedAt: TimeInterval = 0
    private(set) var phase: LinkPhase = .connecting
    private(set) var detail: String = ""
    private(set) var phaseSince: TimeInterval = 0

    private(set) var rssi: Int?
    private(set) var rssiTime: TimeInterval = 0
    private(set) var distance: Float?
    private(set) var distanceTime: TimeInterval = 0
    private(set) var direction: simd_float3?
    private(set) var horizontalAngle: Float?
    private(set) var angleTime: TimeInterval = 0
    /// When the first distance of this connection arrived (0 = none yet). Used by "take turns".
    private(set) var firstDistanceAt: TimeInterval = 0

    /// Last two bytes of the accessory configuration data, as 4 hex digits. The research
    /// note says they follow the board's UWB short address, so they should be the same
    /// on every phone for the same board. The first value seen on this connection is kept.
    private(set) var addressSuffix: String?
    private(set) var suffixChanged = false
    private(set) var keyIsFinal = false
    private(set) var hitSessionLimit = false

    private var rxCharacteristic: CBCharacteristic?
    private var txCharacteristic: CBCharacteristic?
    private var niSession: NISession?
    private var configuration: NINearbyAccessoryConfiguration?
    private var initAttempts = 0
    private var lastInitSent: TimeInterval = 0
    private var stopRequested = false
    private var invalidationRetries = 0
    private var gaveUp = false

    /// The manager drops the board (disconnects it and does not retry it for `cooldown` s).
    var onGiveUp: ((PuckLink, String, TimeInterval) -> Void)?
    /// The person refused Nearby Interaction for this app.
    var onPermissionDenied: (() -> Void)?

    init(peripheral: CBPeripheral, advertisedName: String, nameOnlyKey: Bool,
         cameraAssist: Bool, uwbSupported: Bool) {
        self.peripheral = peripheral
        self.advertisedName = advertisedName
        self.nameOnlyKey = nameOnlyKey
        self.wantsCameraAssist = cameraAssist
        self.uwbSupported = uwbSupported
        self.connectStartedAt = nowSeconds()
        super.init()
        phaseSince = connectStartedAt
    }

    var identifier: UUID {
        return peripheral.identifier
    }

    /// The accessory key sent to the server: "<advertised name>#<ABCD>", or the name alone.
    var key: String {
        if !nameOnlyKey, let s = addressSuffix {
            return advertisedName + "#" + s
        }
        return advertisedName
    }

    // MARK: Called by PuckManager

    /// After `centralManager(_:didConnect:)`. Same steps as the sample: become the
    /// peripheral's delegate, then discover only our service(s).
    func didConnect() {
        connectedAt = nowSeconds()
        setPhase(.discovering, "")
        peripheral.delegate = self
        peripheral.discoverServices(PuckUUIDs.services)
    }

    func readRSSIIfConnected() {
        if peripheral.state == .connected {
            peripheral.readRSSI()
        }
    }

    /// Once a second: retries and watchdogs.
    func tick(now: TimeInterval) {
        if gaveUp {
            return
        }
        switch phase {
        case .discovering, .subscribing:
            if rxCharacteristic != nil && initAttempts == 0 && now - connectedAt > 3 {
                // No notification-state callback arrived; ask the board anyway.
                sendInitialize(now)
            } else if now - connectedAt > 12 && connectedAt > 0 {
                giveUp("could not open the board's Nearby Interaction service", cooldown: 20)
            }
        case .waitingForConfig:
            if now - lastInitSent > 4 {
                if initAttempts < 3 {
                    sendInitialize(now)
                } else {
                    giveUp("no answer from the board (another phone may be using it, or it runs other firmware)", cooldown: 30)
                }
            }
        case .startingSession:
            if now - phaseSince > 20 {
                giveUp("UWB never started with this board", cooldown: 15)
            }
        case .accessoryStopped:
            if !stopRequested && now - phaseSince > 2 {
                sendInitialize(now)
            }
        default:
            break
        }
        // If the configuration never arrives, settle on the name alone after 10 s.
        if !keyIsFinal && connectedAt > 0 && now - connectedAt > 10 {
            keyIsFinal = true
        }
    }

    /// Ends this board's UWB session. `sendStop` also asks the board to stop (0x0C).
    func teardown(sendStop: Bool) {
        if sendStop {
            stopRequested = true
            send(Data([MessageId.stop.rawValue]))
        }
        niSession?.invalidate()
        niSession = nil
        configuration = nil
        distance = nil
        direction = nil
        horizontalAngle = nil
    }

    func reading(now: TimeInterval) -> PuckReading {
        var d: Double? = nil
        if let x = distance {
            d = Double(x)
        }
        var r: Double? = nil
        if let x = rssi {
            r = Double(x)
        }
        var az: Double? = nil
        var source: String? = nil
        if now - angleTime < 1.0 {
            if let v = direction {
                // Apple: x points right, z points out of the back of the phone as negative.
                az = atan2(Double(v.x), Double(-v.z)) * 180.0 / Double.pi
                source = "ni-direction+compass"
            } else if let a = horizontalAngle {
                az = Double(a) * 180.0 / Double.pi
                source = "camera-assist+compass"
            }
        }
        return PuckReading(key: key, distance: d, distanceTime: distanceTime, rssi: r,
                           rssiTime: rssiTime, azimuthDeg: az, azimuthSource: source)
    }

    // MARK: Private helpers

    private func setPhase(_ p: LinkPhase, _ text: String) {
        phase = p
        detail = text
        phaseSince = nowSeconds()
    }

    private func log(_ text: String) {
        BeaconLog.shared.add(advertisedName + ": " + text)
    }

    private func giveUp(_ reason: String, cooldown: TimeInterval) {
        if gaveUp {
            return
        }
        gaveUp = true
        setPhase(.failed, reason)
        onGiveUp?(self, reason, cooldown)
    }

    private func sendInitialize(_ now: TimeInterval) {
        initAttempts += 1
        lastInitSent = now
        stopRequested = false
        setPhase(.waitingForConfig, initAttempts > 1 ? "attempt \(initAttempts)" : "")
        send(Data([MessageId.initialize.rawValue]))
    }

    private func canWrite(_ characteristic: CBCharacteristic) -> Bool {
        let props = characteristic.properties
        return props.contains(.write) || props.contains(.writeWithoutResponse)
    }

    /// Writes one message to the board's rx characteristic.
    /// Like the sample: truncate to maximumWriteValueLength and write in one go.
    /// Differs from the sample: the sample always writes `.withResponse`; we use
    /// `.withResponse` when the characteristic allows it and `.withoutResponse` otherwise.
    private func send(_ data: Data) {
        guard let rx = rxCharacteristic, peripheral.state == .connected else {
            return
        }
        let writeType: CBCharacteristicWriteType
        if rx.properties.contains(.write) {
            writeType = .withResponse
        } else if rx.properties.contains(.writeWithoutResponse) {
            writeType = .withoutResponse
        } else {
            writeType = .withResponse
        }
        let mtu = peripheral.maximumWriteValueLength(for: writeType)
        var packet = data
        if data.count > mtu {
            log("message of \(data.count) bytes truncated to \(mtu) (Bluetooth packet size)")
            packet = Data(data.prefix(mtu))
        }
        peripheral.writeValue(packet, for: rx, type: writeType)
    }

    /// Handles one message from the board.
    private func handleMessage(_ data: Data) {
        let bytes = [UInt8](data)
        guard let first = bytes.first else {
            return
        }
        // Differs from the sample: the sample calls fatalError on an unknown id. Newer
        // Qorvo firmware may send extra ids, so we log and ignore them.
        guard let messageId = MessageId(rawValue: first) else {
            log("ignored message id 0x" + String(format: "%02x", first) + " (\(bytes.count) bytes)")
            return
        }
        switch messageId {
        case .accessoryConfigurationData:
            guard bytes.count > 1 else {
                log("configuration message was empty")
                return
            }
            // Same as the sample's data.advanced(by: 1): drop the message id byte.
            let payload = Data(bytes[1...])
            startSession(with: payload)
        case .accessoryUwbDidStart:
            log("board says UWB started")
            if phase != .ranging {
                setPhase(.startingSession, "board started UWB")
            }
        case .accessoryUwbDidStop:
            log("board says UWB stopped")
            if phase != .waitingForConfig {
                distance = nil
                setPhase(.accessoryStopped, stopRequested ? "" : "restarting")
            }
        case .initialize, .configureAndStart, .stop:
            log("board sent a phone-side message id; ignored")
        }
    }

    /// The sample's setupAccessory(_:name:): build the configuration and run a session.
    private func startSession(with payload: Data) {
        let bytes = [UInt8](payload)
        if bytes.count >= 2 {
            let suffix = String(format: "%02X%02X", bytes[bytes.count - 2], bytes[bytes.count - 1])
            if addressSuffix == nil {
                addressSuffix = suffix
            } else if let first = addressSuffix, first != suffix {
                suffixChanged = true
                log("configuration suffix changed from " + first + " to " + suffix + "; keeping the first")
            }
        }
        keyIsFinal = true
        initAttempts = 0
        log("configuration data received (\(bytes.count) bytes), key \(key)")

        guard uwbSupported else {
            setPhase(.bluetoothOnly, "this phone has no UWB chip")
            return
        }

        // One session per configuration: drop any previous one first.
        niSession?.invalidate()
        niSession = nil

        let config: NINearbyAccessoryConfiguration
        do {
            config = try NINearbyAccessoryConfiguration(data: payload)
        } catch {
            let code = (error as NSError).code
            giveUp("iOS rejected the board's configuration (error \(code))", cooldown: 30)
            return
        }
        if wantsCameraAssist {
            config.isCameraAssistanceEnabled = true
        }
        configuration = config

        let session = NISession()
        session.delegate = self
        niSession = session
        setPhase(.startingSession, wantsCameraAssist ? "camera assistance on" : "")
        session.run(config)
    }
}

// MARK: - CBPeripheralDelegate (same callbacks as the sample's DataCommunicationChannel)

extension PuckLink: CBPeripheralDelegate {

    func peripheral(_ peripheral: CBPeripheral, didModifyServices invalidatedServices: [CBService]) {
        for service in invalidatedServices {
            if PuckUUIDs.services.contains(service.uuid) {
                log("service invalidated; discovering again")
                rxCharacteristic = nil
                txCharacteristic = nil
                peripheral.discoverServices(PuckUUIDs.services)
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error = error {
            giveUp("service discovery failed: \(error.localizedDescription)", cooldown: 10)
            return
        }
        guard let services = peripheral.services else {
            return
        }
        var found = false
        for service in services {
            if service.uuid == PuckUUIDs.qorvoService {
                found = true
                peripheral.discoverCharacteristics([PuckUUIDs.qorvoRx, PuckUUIDs.qorvoTx], for: service)
            } else if service.uuid == PuckUUIDs.nordicService {
                found = true
                peripheral.discoverCharacteristics([PuckUUIDs.nordicRx, PuckUUIDs.nordicTx], for: service)
            }
        }
        if found {
            setPhase(.subscribing, "")
        } else {
            giveUp("the board has no Nearby Interaction service", cooldown: 30)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error = error {
            giveUp("characteristic discovery failed: \(error.localizedDescription)", cooldown: 10)
            return
        }
        guard let characteristics = service.characteristics else {
            return
        }
        for characteristic in characteristics {
            if PuckUUIDs.rxAll.contains(characteristic.uuid) && rxCharacteristic == nil {
                rxCharacteristic = characteristic
            }
            if PuckUUIDs.txAll.contains(characteristic.uuid) && txCharacteristic == nil {
                txCharacteristic = characteristic
            }
        }
        // Differs from the sample: the sample always writes to "rx". If "rx" cannot be
        // written but "tx" can (UUIDs named from the other side's point of view), write
        // to "tx" instead, so the board still receives initialize (0x0A).
        if let rx = rxCharacteristic, let tx = txCharacteristic, !canWrite(rx), canWrite(tx) {
            rxCharacteristic = tx
            txCharacteristic = rx
            log("rx is not writable but tx is: writing to tx")
        }
        // Differs from the sample: the sample calls setNotifyValue(true) on both rx and tx.
        // We call it on whichever of the two can notify or indicate (normally tx only).
        var toSubscribe: [CBCharacteristic] = []
        if let tx = txCharacteristic {
            toSubscribe.append(tx)
        }
        if let rx = rxCharacteristic {
            toSubscribe.append(rx)
        }
        for characteristic in toSubscribe {
            let props = characteristic.properties
            if props.contains(.notify) || props.contains(.indicate) {
                peripheral.setNotifyValue(true, for: characteristic)
            }
        }
        if rxCharacteristic == nil {
            giveUp("the board has no writable characteristic", cooldown: 30)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error = error {
            log("could not subscribe: \(error.localizedDescription)")
            return
        }
        if characteristic.isNotifying {
            // Differs from the sample: the sample waits for a button press before sending
            // initialize. We send it as soon as the board can answer.
            if initAttempts == 0 {
                sendInitialize(nowSeconds())
            }
        } else {
            // The sample disconnects when notifications stop.
            giveUp("the board stopped notifications", cooldown: 5)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error = error {
            log("receive error: \(error.localizedDescription)")
            return
        }
        guard let value = characteristic.value else {
            return
        }
        handleMessage(value)
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error = error {
            log("write failed: \(error.localizedDescription)")
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didReadRSSI RSSI: NSNumber, error: Error?) {
        if error != nil {
            return
        }
        let value = RSSI.intValue
        if value == 127 {
            // 127 means "not available" in Core Bluetooth.
            return
        }
        rssi = value
        rssiTime = nowSeconds()
    }
}

// MARK: - NISessionDelegate (same callbacks as the sample's AccessoryDemoViewController)

extension PuckLink: NISessionDelegate {

    func session(_ session: NISession, didGenerateShareableConfigurationData shareableConfigurationData: Data, for object: NINearbyObject) {
        guard session === niSession else {
            return
        }
        guard object.discoveryToken == configuration?.accessoryDiscoveryToken else {
            return
        }
        var message = Data([MessageId.configureAndStart.rawValue])
        message.append(shareableConfigurationData)
        send(message)
        log("sent shareable configuration (\(message.count) bytes)")
        detail = "sent our UWB settings"
    }

    func session(_ session: NISession, didUpdate nearbyObjects: [NINearbyObject]) {
        guard session === niSession else {
            return
        }
        let token = configuration?.accessoryDiscoveryToken
        let now = nowSeconds()
        for object in nearbyObjects {
            if let t = token, object.discoveryToken != t {
                continue
            }
            if let d = object.distance {
                distance = d
                distanceTime = now
                if firstDistanceAt == 0 {
                    firstDistanceAt = now
                }
                if phase != .ranging {
                    setPhase(.ranging, "")
                    invalidationRetries = 0
                }
            }
            // Keep the latest direction estimate; nil is expected on iPhone 14 and 15.
            direction = object.direction
            horizontalAngle = object.horizontalAngle
            if direction != nil || horizontalAngle != nil {
                angleTime = now
            }
        }
    }

    func session(_ session: NISession, didRemove nearbyObjects: [NINearbyObject], reason: NINearbyObject.RemovalReason) {
        guard session === niSession else {
            return
        }
        distance = nil
        switch reason {
        case .timeout:
            // Same as the sample: ask the board to stop, then to start again.
            log("UWB session timed out; restarting")
            stopRequested = true
            send(Data([MessageId.stop.rawValue]))
            sendInitialize(nowSeconds())
        case .peerEnded:
            log("the board ended the UWB session")
            setPhase(.accessoryStopped, "restarting")
        @unknown default:
            log("UWB object removed (unknown reason)")
        }
    }

    func sessionWasSuspended(_ session: NISession) {
        guard session === niSession else {
            return
        }
        // Same as the sample: tell the board to stop while we are suspended.
        setPhase(.suspended, "app not in front: keep this screen open")
        stopRequested = true
        send(Data([MessageId.stop.rawValue]))
    }

    func sessionSuspensionEnded(_ session: NISession) {
        guard session === niSession else {
            return
        }
        // Same as the sample: restart the configuration procedure with the board.
        sendInitialize(nowSeconds())
    }

    func session(_ session: NISession, didInvalidateWith error: Error) {
        guard session === niSession else {
            return
        }
        niSession = nil
        distance = nil
        let code = (error as NSError).code
        switch error {
        case NIError.invalidConfiguration:
            giveUp("iOS says the board's configuration is invalid (error \(code))", cooldown: 30)
        case NIError.userDidNotAllow:
            setPhase(.failed, "Nearby Interaction not allowed for this app")
            log("Nearby Interaction permission denied")
            onPermissionDenied?()
        default:
            if code == NIError.Code.activeSessionsLimitExceeded.rawValue {
                hitSessionLimit = true
                giveUp("this phone cannot run more UWB sessions at once", cooldown: 120)
                return
            }
            // Same as the sample's handleSessionInvalidation(): stop, then initialize again
            // (a new NISession is created when the configuration arrives).
            invalidationRetries += 1
            if invalidationRetries <= 3 {
                log("UWB session invalidated (error \(code)); retry \(invalidationRetries)")
                stopRequested = true
                send(Data([MessageId.stop.rawValue]))
                sendInitialize(nowSeconds())
            } else {
                giveUp("UWB session keeps failing (error \(code))", cooldown: 20)
            }
        }
    }
}
