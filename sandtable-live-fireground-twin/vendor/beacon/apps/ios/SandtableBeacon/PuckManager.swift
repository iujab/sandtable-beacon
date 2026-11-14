// SandtableBeacon - PuckManager.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// The Core Bluetooth central: scans for rescue boards, connects to at most
// `maxPucks` of them (strongest signal first), reads each connection's signal strength
// once a second, and drops and rescans boards that fail.
//
// The CBCentralManager calls are modelled on Apple's sample "Implementing spatial
// interactions with third-party accessories" (BluetoothLECentral.swift: init with
// CBCentralManagerOptionShowPowerAlertKey, retrieveConnectedPeripherals, scanning with
// CBCentralManagerScanOptionAllowDuplicatesKey, rescanning after a disconnect).
// Our own code, not a copy. The sample handles ONE accessory; this handles several,
// each with its own NISession (Apple: U1 and U2 phones support multiple accessory
// sessions at once; one NISession per accessory).
//
// Threading: queue nil = every callback on the main queue.

import Foundation
import CoreBluetooth
import NearbyInteraction

/// One line of the "Rescue beacons" list.
struct PuckRow: Identifiable {
    let id: UUID
    let key: String
    let keyFinal: Bool
    let rssi: Int?
    let distance: Float?
    let distanceAge: Double?
    let phase: String
    let detail: String
    let cameraAssist: Bool
}

/// A board heard advertising but not connected (yet).
struct PuckCandidate {
    let peripheral: CBPeripheral
    var name: String
    var rssi: Int
    var lastSeen: TimeInterval
}

/// What this phone's Nearby Interaction hardware supports, for the setup screen.
enum PhoneCapabilities {
    static var uwbDistance: Bool {
        return NISession.deviceCapabilities.supportsPreciseDistanceMeasurement
    }

    static func line() -> String {
        let caps = NISession.deviceCapabilities
        return "UWB distance: " + yesNo(caps.supportsPreciseDistanceMeasurement)
            + "  ·  direction: " + yesNo(caps.supportsDirectionMeasurement)
            + "  ·  camera assistance: " + yesNo(caps.supportsCameraAssistance)
    }

    private static func yesNo(_ value: Bool) -> String {
        return value ? "yes" : "no"
    }
}

final class PuckManager: NSObject, ObservableObject {
    @Published private(set) var rows: [PuckRow] = []
    @Published private(set) var isRunning = false
    @Published private(set) var bluetoothText: String = "Bluetooth not started"
    @Published private(set) var boardsHeard: Int = 0
    @Published private(set) var nearbyPermissionDenied = false
    @Published private(set) var warning: String?
    @Published private(set) var lastProblem: String?

    private let settings: AppSettings
    private var central: CBCentralManager?
    private var links: [PuckLink] = []
    private var candidates: [UUID: PuckCandidate] = [:]
    private var cooldownUntil: [UUID: TimeInterval] = [:]
    private var timer: Timer?
    private var tickCount = 0
    private var sessionCap = 5
    private var loggedCapabilities = false

    init(settings: AppSettings) {
        self.settings = settings
        super.init()
    }

    // MARK: Start and stop

    func start() {
        if isRunning {
            return
        }
        isRunning = true
        if !loggedCapabilities {
            loggedCapabilities = true
            BeaconLog.shared.add("This phone: " + PhoneInfo.modelName + " (" + PhoneInfo.identifier + "). " + PhoneCapabilities.line())
        }
        if central == nil {
            // Same options as the sample. Creating the manager shows the Bluetooth prompt
            // the first time; scanning starts in centralManagerDidUpdateState.
            central = CBCentralManager(delegate: self, queue: nil,
                                       options: [CBCentralManagerOptionShowPowerAlertKey: true])
        } else {
            startScanningIfReady()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.tick()
        }
        BeaconLog.shared.add("Looking for rescue beacons (at most \(settings.maxPucks))")
    }

    func stop() {
        if !isRunning {
            return
        }
        isRunning = false
        timer?.invalidate()
        timer = nil
        if let c = central, c.isScanning {
            c.stopScan()
        }
        for link in links {
            link.teardown(sendStop: true)
            central?.cancelPeripheralConnection(link.peripheral)
        }
        links.removeAll()
        candidates.removeAll()
        refreshRows()
        BeaconLog.shared.add("Stopped looking for rescue beacons")
    }

    /// The latest values of every board whose key is settled, for the reporter.
    func readings(now: TimeInterval) -> [PuckReading] {
        var result: [PuckReading] = []
        for link in links {
            if link.keyIsFinal {
                result.append(link.reading(now: now))
            }
        }
        return result
    }

    // MARK: Timer

    private func tick() {
        tickCount += 1
        let now = nowSeconds()
        if tickCount % 4 == 0 {
            // Once a second: signal strength, per-board watchdogs, new connections.
            for link in links {
                link.readRSSIIfConnected()
                link.tick(now: now)
            }
            dropStalledConnects(now)
            pumpConnections(now)
        }
        refreshRows()
    }

    private func dropStalledConnects(_ now: TimeInterval) {
        for link in links {
            if link.connectedAt == 0 && now - link.connectStartedAt > 8 {
                drop(link, reason: "connection timed out", cooldown: 10)
            }
        }
    }

    /// Connects to the strongest boards heard recently until `maxPucks` are connected.
    private func pumpConnections(_ now: TimeInterval) {
        guard isRunning, let c = central, c.state == .poweredOn else {
            return
        }
        let cap = min(settings.maxPucks, sessionCap)
        if links.count >= cap {
            if c.isScanning {
                c.stopScan()
            }
            return
        }
        startScanningIfReady()

        var connected = Set<UUID>()
        for link in links {
            connected.insert(link.identifier)
        }
        var fresh: [PuckCandidate] = []
        for candidate in candidates.values {
            let id = candidate.peripheral.identifier
            let cooling = (cooldownUntil[id] ?? 0) > now
            if now - candidate.lastSeen < 4 && !connected.contains(id) && !cooling {
                fresh.append(candidate)
            }
        }
        fresh.sort { $0.rssi > $1.rssi }
        var slots = cap - links.count
        for candidate in fresh {
            if slots <= 0 {
                break
            }
            connect(candidate)
            slots -= 1
        }
    }

    private func startScanningIfReady() {
        guard isRunning, let c = central, c.state == .poweredOn, !c.isScanning else {
            return
        }
        // Same call as the sample, with both service UUIDs.
        c.scanForPeripherals(withServices: PuckUUIDs.services,
                             options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
    }

    /// Like the sample's retrievePeripheral(): boards the system already has connected
    /// do not advertise, so pick them up here.
    private func addAlreadyConnectedBoards() {
        guard let c = central else {
            return
        }
        let already = c.retrieveConnectedPeripherals(withServices: PuckUUIDs.services)
        for peripheral in already {
            if findLink(peripheral.identifier) == nil {
                candidates[peripheral.identifier] = PuckCandidate(peripheral: peripheral,
                                                                  name: peripheral.name ?? "Puck",
                                                                  rssi: -50,
                                                                  lastSeen: nowSeconds())
            }
        }
    }

    private func connect(_ candidate: PuckCandidate) {
        guard let c = central else {
            return
        }
        // Camera assistance uses ARKit and the camera, so at most ONE board gets it.
        var assistTaken = false
        for link in links {
            if link.wantsCameraAssist {
                assistTaken = true
            }
        }
        let assist = settings.cameraAssist && !assistTaken
        let link = PuckLink(peripheral: candidate.peripheral,
                            advertisedName: candidate.name,
                            nameOnlyKey: settings.nameOnlyKey,
                            cameraAssist: assist,
                            uwbSupported: PhoneCapabilities.uwbDistance)
        link.onGiveUp = { [weak self] failed, reason, cooldown in
            self?.drop(failed, reason: reason, cooldown: cooldown)
        }
        link.onPermissionDenied = { [weak self] in
            self?.nearbyPermissionDenied = true
        }
        links.append(link)
        BeaconLog.shared.add("Connecting to \(candidate.name) (signal \(candidate.rssi) dBm)")
        c.connect(candidate.peripheral, options: nil)
    }

    /// Disconnects a board and does not try it again for `cooldown` seconds, so that
    /// this phone moves on to another board (two phones share five boards).
    private func drop(_ link: PuckLink, reason: String, cooldown: TimeInterval) {
        BeaconLog.shared.add(link.key + ": " + reason)
        lastProblem = link.key + ": " + reason
        if link.hitSessionLimit {
            sessionCap = max(1, links.count - 1)
            warning = "This phone accepts only \(sessionCap) UWB session(s) at once."
        }
        link.teardown(sendStop: true)
        central?.cancelPeripheralConnection(link.peripheral)
        links.removeAll { $0 === link }
        cooldownUntil[link.identifier] = nowSeconds() + cooldown
        refreshRows()
    }

    private func findLink(_ id: UUID) -> PuckLink? {
        for link in links {
            if link.identifier == id {
                return link
            }
        }
        return nil
    }

    private func refreshRows() {
        let now = nowSeconds()
        var newRows: [PuckRow] = []
        var keys: [String] = []
        var suffixUnstable = false
        for link in links {
            var age: Double? = nil
            if link.distance != nil {
                age = now - link.distanceTime
            }
            newRows.append(PuckRow(id: link.identifier,
                                   key: link.key,
                                   keyFinal: link.keyIsFinal,
                                   rssi: link.rssi,
                                   distance: link.distance,
                                   distanceAge: age,
                                   phase: link.phase.rawValue,
                                   detail: link.detail,
                                   cameraAssist: link.wantsCameraAssist))
            if link.keyIsFinal {
                keys.append(link.key)
            }
            if link.suffixChanged {
                suffixUnstable = true
            }
        }
        rows = newRows

        var heard = 0
        for candidate in candidates.values {
            if now - candidate.lastSeen < 5 {
                heard += 1
            }
        }
        boardsHeard = max(heard, links.count)

        var newWarning: String? = nil
        if Set(keys).count < keys.count {
            newWarning = "Two boards report the same key. Turn OFF \"name only\" in Setup, or rename the boards."
        } else if suffixUnstable {
            newWarning = "A board's key suffix changed between sessions. The suffix may not be stable on this firmware."
        } else if sessionCap < 5 {
            newWarning = "This phone accepts only \(sessionCap) UWB session(s) at once."
        }
        if newWarning != warning {
            warning = newWarning
        }
    }
}

// MARK: - CBCentralManagerDelegate (same callbacks as the sample)

extension PuckManager: CBCentralManagerDelegate {

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            bluetoothText = "Bluetooth on"
            if isRunning {
                addAlreadyConnectedBoards()
                startScanningIfReady()
            }
        case .poweredOff:
            bluetoothText = "Bluetooth is off. Turn it on in Control Center."
        case .resetting:
            bluetoothText = "Bluetooth is restarting"
        case .unauthorized:
            bluetoothText = "Bluetooth not allowed. Settings > Privacy & Security > Bluetooth."
        case .unknown:
            bluetoothText = "Bluetooth state unknown"
        case .unsupported:
            bluetoothText = "This device has no Bluetooth LE"
        @unknown default:
            bluetoothText = "Bluetooth state not recognised"
        }
        BeaconLog.shared.add(bluetoothText)
        if central.state != .poweredOn {
            // Connections are gone when Bluetooth is not on.
            for link in links {
                link.teardown(sendStop: false)
            }
            links.removeAll()
            refreshRows()
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let value = RSSI.intValue
        if value == 127 {
            return
        }
        // Same as the sample: the advertised local name, else the peripheral's name.
        let advertisedName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let name = advertisedName ?? peripheral.name ?? "Puck"
        let now = nowSeconds()
        if var existing = candidates[peripheral.identifier] {
            existing.rssi = (existing.rssi * 2 + value) / 3
            existing.lastSeen = now
            if advertisedName != nil {
                existing.name = name
            }
            candidates[peripheral.identifier] = existing
        } else {
            candidates[peripheral.identifier] = PuckCandidate(peripheral: peripheral, name: name,
                                                              rssi: value, lastSeen: now)
            BeaconLog.shared.add("Heard \(name) (signal \(value) dBm)")
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard let link = findLink(peripheral.identifier) else {
            central.cancelPeripheralConnection(peripheral)
            return
        }
        BeaconLog.shared.add("Connected to \(link.advertisedName)")
        // Differs from the sample: the sample stops scanning here. We keep scanning until
        // maxPucks boards are connected (pumpConnections stops it).
        link.didConnect()
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        if let link = findLink(peripheral.identifier) {
            let why = error?.localizedDescription ?? "no reason given"
            drop(link, reason: "could not connect (" + why + ")", cooldown: 5)
        }
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        if let link = findLink(peripheral.identifier) {
            BeaconLog.shared.add(link.key + " disconnected")
            link.teardown(sendStop: false)
            links.removeAll { $0 === link }
            cooldownUntil[peripheral.identifier] = nowSeconds() + 2
        }
        // Same as the sample: resume scanning after a disconnect.
        startScanningIfReady()
        refreshRows()
    }
}
