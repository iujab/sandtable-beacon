// SandtableBeacon - Models.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// What is in here: the occupant's status, the JSON shapes sent to and received from
// the Beacon server (docs/BEACON_FEED.md, "v1 additions" A2 and A5), the suite
// directory, the settings kept in UserDefaults, the phone model table, and a small
// on-screen event log.
//
// The wire structs use snake_case property names ON PURPOSE: the Swift names are the
// JSON names, so there is no key-mapping step that could go wrong.

import Foundation
import Combine

// MARK: - Time and rounding helpers

/// Unix seconds, as the server expects in `t`.
func nowSeconds() -> TimeInterval {
    return Date().timeIntervalSince1970
}

func round3(_ x: Double) -> Double {
    return (x * 1000.0).rounded() / 1000.0
}

func round1(_ x: Double) -> Double {
    return (x * 10.0).rounded() / 10.0
}

/// Normalises an angle in degrees to 0 ..< 360.
func normalizeDegrees(_ x: Double) -> Double {
    var a = x.truncatingRemainder(dividingBy: 360.0)
    if a < 0 {
        a += 360.0
    }
    return a
}

// MARK: - Occupant status

/// The status values the server understands (BEACON_FEED 2.2 `report.status`).
enum OccupantStatus: String {
    case trapped
    case injured
    case sheltering
    case ok
}

/// The four big buttons on the screen. "I am out" is sent as status `ok`
/// with `safe_outside: true`.
enum StatusChoice: String, CaseIterable, Identifiable {
    case trapped
    case injured
    case sheltering
    case out

    var id: String {
        return rawValue
    }

    var title: String {
        switch self {
        case .trapped:
            return "Trapped"
        case .injured:
            return "Injured"
        case .sheltering:
            return "Sheltering"
        case .out:
            return "I am out"
        }
    }

    var subtitle: String {
        switch self {
        case .trapped:
            return "I cannot get out"
        case .injured:
            return "Someone is hurt"
        case .sheltering:
            return "Safe for now, staying put"
        case .out:
            return "I left the building"
        }
    }

    var systemImage: String {
        switch self {
        case .trapped:
            return "exclamationmark.triangle.fill"
        case .injured:
            return "cross.case.fill"
        case .sheltering:
            return "house.fill"
        case .out:
            return "figure.walk"
        }
    }

    var wireStatus: OccupantStatus {
        switch self {
        case .trapped:
            return .trapped
        case .injured:
            return .injured
        case .sheltering:
            return .sheltering
        case .out:
            return .ok
        }
    }

    var safeOutside: Bool {
        return self == .out
    }
}

/// What the person typed or tapped on the main screen.
final class Occupant: ObservableObject {
    @Published var choice: StatusChoice = .trapped
    @Published var people: Int = 1
    @Published var canMove: Bool = false
    @Published var note: String = ""

    /// The note as sent: trimmed, at most 120 characters (BEACON_FEED 2.2), nil when empty.
    var noteForWire: String? {
        let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return nil
        }
        return String(trimmed.prefix(120))
    }
}

// MARK: - Wire format: POST /api/ingest (BEACON_FEED A2)

struct WireDevice: Codable {
    var eid: String
    var platform: String
    var model: String?
    var chip: String?
    var app: String
}

struct WireReport: Codable {
    var status: String
    var people: Int
    var can_move: Bool
    var safe_outside: Bool
    var platform: String
    var app: String
    var battery_pct: Int?
    var floor_reported: Int?
    var baro_hpa: Double?
    var still: Bool?
    var still_s: Double?
    var home_suite: String?
    var text: String?
    var model: String?
    var chip: String?
    var network: String?

    init(status: String, people: Int, can_move: Bool, safe_outside: Bool, platform: String, app: String) {
        self.status = status
        self.people = people
        self.can_move = can_move
        self.safe_outside = safe_outside
        self.platform = platform
        self.app = app
    }
}

/// One observation. Which fields are set depends on `kind`:
/// - "uwb":     accessory, d_m
/// - "sos":     accessory, rssi
/// - "bearing": accessory, to_puck_compass_deg, sigma_deg, source
/// - "report":  prev_eid (only right after the random id rotated), report
/// - "bye":     nothing else (the person pressed "Stop sharing")
/// Optional fields that are nil are left out of the JSON.
struct WireObservation: Codable {
    var kind: String
    var t: Double
    var eid: String?
    var accessory: String?
    var d_m: Double?
    var rssi: Double?
    var to_puck_compass_deg: Double?
    var sigma_deg: Double?
    var source: String?
    var prev_eid: String?
    var report: WireReport?

    init(kind: String, t: Double, eid: String?) {
        self.kind = kind
        self.t = t
        self.eid = eid
    }
}

struct IngestBatch: Codable {
    var device: WireDevice
    var obs: [WireObservation]
}

/// The server's answer. Every field is optional so a smaller answer still decodes.
struct WireAck: Codable {
    var heard_by: [String]?
    var geometry: String?
    var sigma_m: Double?
    var text: String?
}

struct IngestResponse: Codable {
    var ok: Bool?
    var accepted: Int?
    var unknown_accessories: [String]?
    var ack: WireAck?
}

// MARK: - Suite directory: GET /api/directory

/// One entry of the building's suite list. The server may send `floor` and `suite`
/// as strings or as numbers, so both are accepted.
struct DirectoryEntry: Decodable, Identifiable {
    var suite: String
    var floor: String?
    var label: String?

    var id: String {
        return suite
    }

    enum CodingKeys: String, CodingKey {
        case suite
        case floor
        case label
    }

    init(suite: String, floor: String?, label: String?) {
        self.suite = suite
        self.floor = floor
        self.label = label
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let s = try? c.decode(String.self, forKey: .suite) {
            suite = s
        } else if let n = try? c.decode(Int.self, forKey: .suite) {
            suite = String(n)
        } else {
            suite = "?"
        }
        if let f = try? c.decode(String.self, forKey: .floor) {
            floor = f
        } else if let n = try? c.decode(Int.self, forKey: .floor) {
            floor = String(n)
        } else {
            floor = nil
        }
        label = try? c.decode(String.self, forKey: .label)
    }

    /// "f3" -> 3, "3" -> 3, nil when there are no digits.
    var floorNumber: Int? {
        guard let f = floor else {
            return nil
        }
        let digits = f.filter { $0.isNumber }
        return Int(digits)
    }

    var displayName: String {
        var text = "Suite " + suite
        if let l = label, !l.isEmpty {
            text = l
        }
        if let n = floorNumber {
            text += "  (floor \(n))"
        }
        return text
    }
}

private struct DirectoryWrapper: Decodable {
    var suites: [DirectoryEntry]?
    var directory: [DirectoryEntry]?
}

enum DirectoryParser {
    /// Accepts a bare list, or an object with a "suites" or "directory" list.
    static func parse(_ data: Data) -> [DirectoryEntry]? {
        let decoder = JSONDecoder()
        if let list = try? decoder.decode([DirectoryEntry].self, from: data) {
            return list
        }
        if let wrapped = try? decoder.decode(DirectoryWrapper.self, from: data) {
            if let s = wrapped.suites {
                return s
            }
            return wrapped.directory
        }
        return nil
    }
}

// MARK: - Settings (UserDefaults)

final class AppSettings: ObservableObject {
    private enum Keys {
        static let server = "beacon.server"
        static let homeSuite = "beacon.homeSuite"
        static let homeFloor = "beacon.homeFloor"
        static let maxPucks = "beacon.maxPucks"
        static let cameraAssist = "beacon.cameraAssist"
        static let flipAngleSign = "beacon.flipAngleSign"
        static let nameOnlyKey = "beacon.nameOnlyKey"
        static let takeTurns = "beacon.takeTurns"
        static let holdSeconds = "beacon.holdSeconds"
        static let disabledBoards = "beacon.disabledBoards"
    }

    /// The Beacon server, e.g. "http://192.168.1.20:1885".
    @Published var serverAddress: String = "" {
        didSet { UserDefaults.standard.set(serverAddress, forKey: Keys.server) }
    }

    /// The suite the person picked at set-up ("310"), or "" when not set.
    @Published var homeSuite: String = "" {
        didSet { UserDefaults.standard.set(homeSuite, forKey: Keys.homeSuite) }
    }

    /// The storey number to report; 0 means "do not say".
    @Published var homeFloor: Int = 0 {
        didSet { UserDefaults.standard.set(homeFloor, forKey: Keys.homeFloor) }
    }

    /// How many boards to connect to at once (1 to 5). Qorvo's stock firmware serves ONE
    /// phone per board at a time and stops advertising while connected (Qorvo forum).
    @Published var maxPucks: Int = 2 {
        didSet { UserDefaults.standard.set(maxPucks, forKey: Keys.maxPucks) }
    }

    /// EXPERIMENTAL: camera-assisted direction plus compass. Off by default.
    @Published var cameraAssist: Bool = false {
        didSet { UserDefaults.standard.set(cameraAssist, forKey: Keys.cameraAssist) }
    }

    /// Apple does not document the sign of `horizontalAngle`. We assume "positive =
    /// to the right"; this flips it if a test on the device shows the opposite.
    @Published var flipAngleSign: Bool = false {
        didSet { UserDefaults.standard.set(flipAngleSign, forKey: Keys.flipAngleSign) }
    }

    /// Report boards by their Bluetooth name only (without "#ABCD").
    @Published var nameOnlyKey: Bool = false {
        didSet { UserDefaults.standard.set(nameOnlyKey, forKey: Keys.nameOnlyKey) }
    }

    /// "Take turns": range ONE board at a time, hold it for `holdSeconds` once distances
    /// arrive, then release it so another phone can use it. For two phones sharing boards.
    @Published var takeTurns: Bool = false {
        didSet { UserDefaults.standard.set(takeTurns, forKey: Keys.takeTurns) }
    }

    /// How long to keep a board in "take turns" mode once its distances arrive (2 to 20 s).
    @Published var holdSeconds: Int = 4 {
        didSet { UserDefaults.standard.set(holdSeconds, forKey: Keys.holdSeconds) }
    }

    /// Boards this phone must NOT use, by CBPeripheral.identifier (uuidString). Default:
    /// every board is used. Lets the owner dedicate boards to a phone.
    @Published var disabledBoards: [String] = [] {
        didSet { UserDefaults.standard.set(disabledBoards, forKey: Keys.disabledBoards) }
    }

    /// Not stored: the confirmation line shown after a QR code set the server.
    @Published var configNotice: String = ""

    init() {
        let d = UserDefaults.standard
        serverAddress = d.string(forKey: Keys.server) ?? ""
        homeSuite = d.string(forKey: Keys.homeSuite) ?? ""
        homeFloor = d.integer(forKey: Keys.homeFloor)
        let m = d.integer(forKey: Keys.maxPucks)
        maxPucks = (m >= 1 && m <= 5) ? m : 2
        cameraAssist = d.bool(forKey: Keys.cameraAssist)
        flipAngleSign = d.bool(forKey: Keys.flipAngleSign)
        nameOnlyKey = d.bool(forKey: Keys.nameOnlyKey)
        takeTurns = d.bool(forKey: Keys.takeTurns)
        let h = d.integer(forKey: Keys.holdSeconds)
        holdSeconds = (h >= 2 && h <= 20) ? h : 4
        disabledBoards = d.stringArray(forKey: Keys.disabledBoards) ?? []
    }

    func isBoardEnabled(_ id: UUID) -> Bool {
        return !disabledBoards.contains(id.uuidString)
    }

    func setBoardEnabled(_ id: UUID, _ enabled: Bool) {
        let key = id.uuidString
        if enabled {
            disabledBoards.removeAll { $0 == key }
        } else if !disabledBoards.contains(key) {
            disabledBoards.append(key)
        }
    }

    /// Handles sandtablebeacon://server?url=http://192.168.1.20:1885 (the QR code on the
    /// board page, opened by the Camera app). Returns true when the server was set.
    @discardableResult
    func applyConfigURL(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "sandtablebeacon" else {
            return false
        }
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              (parts.host ?? "").lowercased() == "server" else {
            configNotice = "This link is not a Beacon server link."
            return false
        }
        var value: String? = nil
        for item in parts.queryItems ?? [] {
            if item.name == "url" {
                value = item.value
            }
        }
        let raw = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard let inner = URLComponents(string: raw),
              let scheme = inner.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let innerHost = inner.host, !innerHost.isEmpty else {
            configNotice = "The QR code did not contain a valid http:// server address."
            return false
        }
        serverAddress = raw
        configNotice = "Server set from QR code: " + normalizedServer
        BeaconLog.shared.add(configNotice)
        return true
    }

    /// "192.168.1.20:1885/" -> "http://192.168.1.20:1885"; "" when nothing is set.
    var normalizedServer: String {
        var s = serverAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty {
            return ""
        }
        let lower = s.lowercased()
        if !lower.hasPrefix("http://") && !lower.hasPrefix("https://") {
            s = "http://" + s
        }
        while s.hasSuffix("/") {
            s.removeLast()
        }
        return s
    }

    func url(_ path: String) -> URL? {
        let base = normalizedServer
        if base.isEmpty {
            return nil
        }
        return URL(string: base + path)
    }

    /// The Info.plist allows plain HTTP to IP addresses and ".local" names only
    /// (NSAllowsLocalNetworking makes iOS ignore NSAllowsArbitraryLoads). Warn when the
    /// address is a full DNS name over plain HTTP, which iOS would block.
    var atsWarning: String? {
        let base = normalizedServer
        guard !base.isEmpty, base.lowercased().hasPrefix("http://") else {
            return nil
        }
        guard let host = URL(string: base)?.host, !host.isEmpty else {
            return "This address does not look valid. Example: http://192.168.1.20:1885"
        }
        if host.contains(":") {
            return nil
        }
        let ipChars = CharacterSet(charactersIn: "0123456789.")
        if host.unicodeScalars.allSatisfy({ ipChars.contains($0) }) {
            return nil
        }
        if host.lowercased().hasSuffix(".local") || !host.contains(".") {
            return nil
        }
        return "iOS blocks plain http:// to a full DNS name in this build. Type the server's IP address instead (the launcher prints it)."
    }
}

// MARK: - This phone

enum PhoneInfo {
    /// "iPhone14,7" on a device. On the simulator, the simulated model's identifier.
    static let identifier: String = PhoneInfo.machineIdentifier()

    private static func machineIdentifier() -> String {
        var systemInfo = utsname()
        uname(&systemInfo)
        let mirror = Mirror(reflecting: systemInfo.machine)
        var result = ""
        for child in mirror.children {
            if let value = child.value as? Int8, value != 0 {
                result.append(Character(UnicodeScalar(UInt8(bitPattern: value))))
            }
        }
        if result == "arm64" || result == "x86_64" {
            if let sim = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] {
                return sim
            }
        }
        return result
    }

    private static let names: [String: String] = [
        "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
        "iPhone12,8": "iPhone SE (2nd generation)",
        "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
        "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
        "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
        "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
        "iPhone14,6": "iPhone SE (3rd generation)",
        "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
        "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
        "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
        "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
        "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
        "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
        "iPhone17,5": "iPhone 16e"
    ]

    /// "iPhone 14", or the raw identifier when the table does not know it.
    static var modelName: String {
        return names[identifier] ?? identifier
    }

    /// "U1" (iPhone 11 to 14), "U2" (iPhone 15 and later), nil for models without UWB.
    /// The caller also checks NISession.deviceCapabilities, which is the real answer.
    static var chipGuess: String? {
        let id = identifier
        guard id.hasPrefix("iPhone") else {
            return nil
        }
        let parts = id.dropFirst(6).split(separator: ",")
        guard parts.count == 2, let major = Int(parts[0]), let minor = Int(parts[1]) else {
            return nil
        }
        if (major == 12 && minor == 8) || (major == 14 && minor == 6) || (major == 17 && minor == 5) {
            return nil
        }
        if major >= 12 && major <= 14 {
            return "U1"
        }
        if major == 15 {
            return (minor == 2 || minor == 3) ? "U1" : "U2"
        }
        if major >= 16 {
            return "U2"
        }
        return nil
    }
}

// MARK: - Event log (shown at the bottom of Setup, for the owner's debugging)

final class BeaconLog: ObservableObject {
    static let shared = BeaconLog()

    @Published private(set) var lines: [String] = []
    private let formatter: DateFormatter

    init() {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        formatter = f
    }

    func add(_ text: String) {
        let line = formatter.string(from: Date()) + "  " + text
        print("[Beacon] " + line)
        lines.append(line)
        if lines.count > 120 {
            lines.removeFirst(lines.count - 120)
        }
    }
}
