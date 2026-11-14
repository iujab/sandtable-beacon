// SandtableBeacon - PuckManager.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// The Core Bluetooth central: scans for rescue boards, connects to at most `maxPucks`
// of them (strongest signal first), reads each connection's signal strength once a
// second, drops and rescans boards that fail, and keeps the latest distance and signal
// of every board for 25 s so the reporter can keep sending them.
//
// Qorvo's stock firmware serves ONE phone per board at a time and stops advertising
// while connected (Qorvo forum, per the lead). Two ways to share boards between phones:
//   - "Take turns" (setting, default off): range ONE board at a time; once its distances
//     arrive, hold it for `holdSeconds`, then send stop (0x0C), disconnect, and stay
//     away from it for a random 4 to 8 s while trying the next board heard. A board that
//     gives no distance within 8 s of connecting is dropped and retried later.
//   - Per-board "use" toggles (stored by CBPeripheral.identifier): dedicate boards.
//
// The CBCentralManager calls are modelled on Apple's sample "Implementing spatial
// interactions with third-party accessories" (BluetoothLECentral.swift: init with
// CBCentralManagerOptionShowPowerAlertKey, retrieveConnectedPeripherals, scanning with
// CBCentralManagerScanOptionAllowDuplicatesKey, rescanning after a disconnect).
// Our own code, not a copy. The sample handles ONE accessory; this handles several,
// each with its own NISession (Apple: one NISession per accessory).
//
// Threading: queue nil = every callback on the main queue; the timer runs on main too.

import Foundation
import Combine
import CoreBluetooth
import NearbyInteraction

/// One line of the "Rescue beacons" list: every board heard, connected or not.
struct PuckRow: Identifiable {
    let id: UUID
    let title: String
    let connected: Bool
    let enabled: Bool
    let rssi: Int?
    let distance: Double?
    let distanceAge: Double?
    let state: String
    let cameraAssist: Bool
}

/// A board heard advertising.
struct PuckCandidate {
    let peripheral: CBPeripheral
    var name: String
    var rssi: Int
    var lastSeen: TimeInterval
}

/// A value with the time it was measured.
struct TimedValue {
    let value: Double
    let t: TimeInterval
}

/// What this phone's Nearby Interaction hardware supports, for the setup screen.
enum PhoneCapabilities {
    static var uwbDistance: Bool {
        return NISession.deviceCapabilities.supportsPreciseDistanceMeasurement
    }

    static func line() -> String {
        let caps = NISession.deviceCapabilities
        let distance = yesNo(caps.supportsPreciseDistanceMeasurement)
        let direction = yesNo(caps.supportsDirectionMeasurement)
        let camera = yesNo(caps.supportsCameraAssistance)
        return "UWB distance: \(distance)  ·  direction: \(direction)  ·  camera assistance: \(camera)"
    }

    private static func yesNo(_ value: Bool) -> String {
        return value ? "yes" : "no"
    }
}

final class PuckManager: NSObject, ObservableObject {
    /// How long a distance or signal reading is still reported after it was measured.
    static let keepReadingsSeconds: TimeInterval = 25
    /// Take turns: a board with no distance this long after connecting is dropped.
    static let firstDistanceTimeout: TimeInterval = 8

    @Published private(set) var rows: [PuckRow] = []
    @Published private(set) var isRunning = false
    @Published private(set) var bluetoothText: String = "Bluetooth not started"
    @Published private(set) var connectedCount: Int = 0
    @Published private(set) var nearbyPermissionDenied = false
    @Published private(set) var warning: String?
    @Published private(set) var lastProblem: String?

    private let settings: AppSettings
    private var central: CBCentralManager?
    private var links: [PuckLink] = []
    private var candidates: [UUID: PuckCandidate] = [:]
    private var cooldownUntil: [UUID: TimeInterval] = [:]
    private var lastTurnEnded: [UUID: TimeInterval] = [:]
    private var knownKeys: [UUID: String] = [:]
    private var distanceCache: [String: TimedValue] = [:]
    private var rssiCache: [String: TimedValue] = [:]
    private var suffixUnstable = false
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
            let caps = PhoneCapabilities.line()
            BeaconLog.shared.add("This phone: \(PhoneInfo.modelName) (\(PhoneInfo.identifier)). \(caps)")
        }
        if central == nil {
            // Same options as the sample. Creating the manager shows the Bluetooth prompt
            // the first time; scanning starts in centralManagerDidUpdateState.
            central = CBCentralManager(delegate: self, queue: nil,
                                       options: [CBCentralManagerOptionShowPowerAlertKey: true])
        } else {
            startScanningIfReady()
        }
        // .common mode: a timer in the default mode stops while a finger scrolls the screen.
        let t = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.tick()
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        let mode = settings.takeTurns ? "taking turns, one board at a time" : "at most \(settings.maxPucks) boards"
        BeaconLog.shared.add("Looking for rescue beacons (" + mode + ")")
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
            releaseConnection(link.peripheral)
        }
        links.removeAll()
        candidates.removeAll()
        distanceCache.removeAll()
        rssiCache.removeAll()
        // Cleared so the banner goes away once Nearby Interaction is allowed in Settings
        // (the next session sets it again if it is still denied).
        nearbyPermissionDenied = false
        refreshRows()
        BeaconLog.shared.add("Stopped looking for rescue beacons")
    }

    /// The "use" toggle of one board. Turning a connected board off releases it now.
    func setBoardEnabled(_ id: UUID, _ enabled: Bool) {
        settings.setBoardEnabled(id, enabled)
        if !enabled, let link = findLink(id) {
            drop(link, reason: "not used by this phone (toggle)", cooldown: 1)
        }
        refreshRows()
    }

    /// The latest distance and signal of every board measured in the last 25 s, with the
    /// time each was measured, plus the live direction estimate of connected boards.
    func readings(now: TimeInterval) -> [PuckReading] {
        var keys: [String] = []
        for key in distanceCache.keys {
            keys.append(key)
        }
        for key in rssiCache.keys {
            if !keys.contains(key) {
                keys.append(key)
            }
        }
        var live: [String: PuckReading] = [:]
        for link in links {
            if link.keyIsFinal {
                live[link.key] = link.reading(now: now)
                if !keys.contains(link.key) {
                    keys.append(link.key)
                }
            }
        }
        var result: [PuckReading] = []
        for key in keys.sorted() {
            var d: Double? = nil
            var dt: TimeInterval = 0
            if let cached = distanceCache[key], now - cached.t <= PuckManager.keepReadingsSeconds {
                d = cached.value
                dt = cached.t
            }
            var r: Double? = nil
            var rt: TimeInterval = 0
            if let cached = rssiCache[key], now - cached.t <= PuckManager.keepReadingsSeconds {
                r = cached.value
                rt = cached.t
            }
            let liveReading = live[key]
            if d == nil && r == nil && liveReading?.azimuthDeg == nil {
                continue
            }
            result.append(PuckReading(key: key, distance: d, distanceTime: dt, rssi: r, rssiTime: rt,
                                      azimuthDeg: liveReading?.azimuthDeg,
                                      azimuthSource: liveReading?.azimuthSource))
        }
        return result
    }

    // MARK: Timer

    private func tick() {
        tickCount += 1
        let now = nowSeconds()
        for link in links {
            capture(link)
        }
        if settings.takeTurns {
            manageTurns(now)
        }
        if tickCount % 4 == 0 {
            // Once a second: signal strength, per-board watchdogs, new connections.
            for link in links {
                link.readRSSIIfConnected()
                link.tick(now: now)
                if !settings.isBoardEnabled(link.identifier) {
                    drop(link, reason: "not used by this phone (toggle)", cooldown: 1)
                }
            }
            dropStalledConnects(now)
            pumpConnections(now)
        }
        if tickCount % 2 == 0 {
            refreshRows()
        }
    }

    /// Copies a board's latest distance and signal into the 25 s cache.
    private func capture(_ link: PuckLink) {
        guard link.keyIsFinal else {
            return
        }
        let key = link.key
        // The same board (same Bluetooth identifier) came back with another "#ABCD" on a
        // new connection: the suffix is not stable on this firmware (Setup warns).
        if let old = knownKeys[link.identifier], let oldSuffix = keySuffix(old),
           let newSuffix = keySuffix(key), oldSuffix != newSuffix, !suffixUnstable {
            suffixUnstable = true
            BeaconLog.shared.add("Key of the same board changed between connections: " + old + " -> " + key)
        }
        knownKeys[link.identifier] = key
        if link.suffixChanged {
            suffixUnstable = true
        }
        if let d = link.distance {
            distanceCache[key] = TimedValue(value: Double(d), t: link.distanceTime)
        }
        if let r = link.rssi {
            rssiCache[key] = TimedValue(value: Double(r), t: link.rssiTime)
        }
    }

    /// "name#1A2B" -> "1A2B"; nil when the key has no suffix.
    private func keySuffix(_ key: String) -> String? {
        guard let hash = key.lastIndex(of: "#") else {
            return nil
        }
        return String(key[key.index(after: hash)...])
    }

    /// Take turns: release a board `holdSeconds` after its first distance, or 8 s after
    /// connecting if no distance came. The released board rests for a random 4 to 8 s.
    private func manageTurns(_ now: TimeInterval) {
        for link in links {
            if link.firstDistanceAt > 0 {
                if now - link.firstDistanceAt >= Double(settings.holdSeconds) {
                    lastTurnEnded[link.identifier] = now
                    drop(link, reason: "turn over", cooldown: Double.random(in: 4...8), quiet: true)
                }
            } else if now - link.connectStartedAt > PuckManager.firstDistanceTimeout {
                lastTurnEnded[link.identifier] = now
                drop(link, reason: "no distance within 8 s; will retry", cooldown: Double.random(in: 4...8))
            }
        }
    }

    private func dropStalledConnects(_ now: TimeInterval) {
        // Taking turns: a board the other phone just took stops advertising, so a pending
        // connection to it would wait forever; give up sooner.
        let limit: TimeInterval = settings.takeTurns ? 4 : 8
        for link in links {
            if link.connectedAt == 0 && now - link.connectStartedAt > limit {
                drop(link, reason: "connection timed out", cooldown: 10)
            }
        }
    }

    /// Connects to boards heard recently until the limit is reached: strongest first, or,
    /// when taking turns, the board whose last turn is oldest first.
    private func pumpConnections(_ now: TimeInterval) {
        guard isRunning, let c = central, c.state == .poweredOn else {
            return
        }
        // Keep scanning all the time: the list shows every board heard, and boards
        // reappear when another phone releases them.
        startScanningIfReady()
        let cap = settings.takeTurns ? 1 : min(settings.maxPucks, sessionCap)
        if links.count >= cap {
            return
        }
        var connected = Set<UUID>()
        for link in links {
            connected.insert(link.identifier)
        }
        var fresh: [PuckCandidate] = []
        for candidate in candidates.values {
            let id = candidate.peripheral.identifier
            let cooling = (cooldownUntil[id] ?? 0) > now
            let enabled = settings.isBoardEnabled(id)
            if now - candidate.lastSeen < 4 && !connected.contains(id) && !cooling && enabled {
                fresh.append(candidate)
            }
        }
        if settings.takeTurns {
            let turns = lastTurnEnded
            fresh.sort { a, b in
                let ta = turns[a.peripheral.identifier] ?? 0
                let tb = turns[b.peripheral.identifier] ?? 0
                if ta != tb {
                    return ta < tb
                }
                return a.rssi > b.rssi
            }
        } else {
            fresh.sort { $0.rssi > $1.rssi }
        }
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
        if !settings.takeTurns {
            BeaconLog.shared.add("Connecting to \(candidate.name) (signal \(candidate.rssi) dBm)")
        }
        c.connect(candidate.peripheral, options: nil)
    }

    /// Releases a board: asks it to stop (0x0C), disconnects, and does not try it again
    /// for `cooldown` seconds, so this phone moves on and another phone can use it.
    private func drop(_ link: PuckLink, reason: String, cooldown: TimeInterval, quiet: Bool = false) {
        guard links.contains(where: { $0 === link }) else {
            return
        }
        capture(link)
        if !quiet {
            BeaconLog.shared.add(link.key + ": " + reason)
            lastProblem = link.key + ": " + reason
        }
        if link.hitSessionLimit {
            sessionCap = max(1, links.count - 1)
        }
        link.teardown(sendStop: true)
        releaseConnection(link.peripheral)
        links.removeAll { $0 === link }
        cooldownUntil[link.identifier] = nowSeconds() + cooldown
        refreshRows()
    }

    /// Disconnects 0.3 s later, so the stop message (0x0C) written just before has a
    /// chance to reach the board.
    private func releaseConnection(_ peripheral: CBPeripheral) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.central?.cancelPeripheralConnection(peripheral)
        }
    }

    private func findLink(_ id: UUID) -> PuckLink? {
        for link in links {
            if link.identifier == id {
                return link
            }
        }
        return nil
    }

    func refreshRows() {
        let now = nowSeconds()
        var newRows: [PuckRow] = []
        var shown = Set<UUID>()
        var connectedKeys: [String] = []

        // Connected boards first.
        for link in links {
            shown.insert(link.identifier)
            var age: Double? = nil
            var d: Double? = nil
            if let x = link.distance {
                d = Double(x)
                age = now - link.distanceTime
            }
            var state = link.phase.rawValue
            if !link.detail.isEmpty {
                state += " · " + link.detail
            }
            newRows.append(PuckRow(id: link.identifier,
                                   title: link.key,
                                   connected: true,
                                   enabled: settings.isBoardEnabled(link.identifier),
                                   rssi: link.rssi,
                                   distance: d,
                                   distanceAge: age,
                                   state: state,
                                   cameraAssist: link.wantsCameraAssist))
            if link.keyIsFinal {
                connectedKeys.append(link.key)
            }
        }

        // Then every other board heard in the last 15 s, strongest first.
        var others: [PuckCandidate] = []
        for candidate in candidates.values {
            let id = candidate.peripheral.identifier
            if !shown.contains(id) && now - candidate.lastSeen < 15 {
                others.append(candidate)
            }
        }
        others.sort { $0.rssi > $1.rssi }
        for candidate in others {
            let id = candidate.peripheral.identifier
            let enabled = settings.isBoardEnabled(id)
            let title = knownKeys[id] ?? candidate.name
            var d: Double? = nil
            var age: Double? = nil
            if let key = knownKeys[id], let cached = distanceCache[key], now - cached.t <= PuckManager.keepReadingsSeconds {
                d = cached.value
                age = now - cached.t
            }
            var state = "Heard"
            if !enabled {
                state = "Not used by this phone"
            } else if (cooldownUntil[id] ?? 0) > now {
                state = settings.takeTurns ? "Waiting for its turn" : "Resting after a problem"
            } else if now - candidate.lastSeen > 4 {
                state = "Not advertising (another phone may be using it)"
            }
            newRows.append(PuckRow(id: id,
                                   title: title,
                                   connected: false,
                                   enabled: enabled,
                                   rssi: candidate.rssi,
                                   distance: d,
                                   distanceAge: age,
                                   state: state,
                                   cameraAssist: false))
        }
        rows = newRows
        if connectedCount != links.count {
            connectedCount = links.count
        }

        var newWarning: String? = nil
        if Set(connectedKeys).count < connectedKeys.count {
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
        // A board whose key is known (it was connected before) keeps a fresh signal
        // reading from its advertisements, e.g. while another phone has its turn.
        if let key = knownKeys[peripheral.identifier] {
            rssiCache[key] = TimedValue(value: Double(value), t: now)
        }
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
        if !settings.takeTurns {
            BeaconLog.shared.add("Connected to \(link.advertisedName)")
        }
        // Differs from the sample: the sample stops scanning here. We keep scanning so the
        // list shows every board and released boards are seen again quickly.
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
            capture(link)
            link.teardown(sendStop: false)
            links.removeAll { $0 === link }
            cooldownUntil[peripheral.identifier] = nowSeconds() + 2
        }
        // Same as the sample: resume scanning after a disconnect.
        startScanningIfReady()
        refreshRows()
    }
}
