// SandtableBeacon - Reporter.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// While SOS is active, every 0.5 s: one JSON batch to <server>/api/ingest in the shape
// of docs/BEACON_FEED.md addition A2:
//   {"device": {"eid", "platform": "ios", "model", "chip", "app"},
//    "obs": [ {"kind": "uwb", "t", "eid", "accessory", "d_m"},          one per board heard in the last 25 s
//             {"kind": "sos", "t", "eid", "accessory", "rssi"},         one per board heard in the last 25 s
//             {"kind": "bearing", "t", "eid", "accessory", "to_puck_compass_deg", "sigma_deg", "source"},
//             {"kind": "report", "t", "eid", "prev_eid"?, "report": {...}} ]}
// and shows the server's `ack`. Plain URLSession with completion handlers, 3 s timeout;
// a failed batch is dropped (never retried, never blocks the UI).
// "Stop sharing" sends {"kind": "bye"} and stops everything.

import Foundation
import UIKit
import NearbyInteraction

final class Reporter: ObservableObject {
    static let appVersion = "0.1"
    static let eidRotateSeconds: TimeInterval = 600
    static let batchInterval: TimeInterval = 0.5
    static let bearingSigmaDeg: Double = 25

    @Published private(set) var isSharing = false
    @Published private(set) var ackText: String = ""
    @Published private(set) var heardBy: [String] = []
    @Published private(set) var serverOK: Bool?
    @Published private(set) var serverDetail: String = ""
    @Published private(set) var unknownAccessories: [String] = []
    @Published private(set) var lastBatchJSON: String = "(nothing sent yet)"
    @Published private(set) var batchesAccepted: Int = 0
    @Published private(set) var eid: String = ""

    let settings: AppSettings
    let pucks: PuckManager
    let sensors: Sensors
    let occupant: Occupant

    private var timer: Timer?
    private var eidStartedAt: TimeInterval = 0
    private var pendingPrevEid: String?
    private var inFlight = 0
    private let urlSession: URLSession
    private let encoder: JSONEncoder
    private let prettyEncoder: JSONEncoder

    init(settings: AppSettings, pucks: PuckManager, sensors: Sensors, occupant: Occupant) {
        self.settings = settings
        self.pucks = pucks
        self.sensors = sensors
        self.occupant = occupant
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        config.timeoutIntervalForResource = 3
        config.waitsForConnectivity = false
        self.urlSession = URLSession(configuration: config)
        self.encoder = JSONEncoder()
        let pretty = JSONEncoder()
        pretty.outputFormatting = [.prettyPrinted, .sortedKeys]
        self.prettyEncoder = pretty
    }

    // MARK: Start and stop

    func start() {
        if isSharing {
            return
        }
        isSharing = true
        let now = nowSeconds()
        if eid.isEmpty || now - eidStartedAt > Reporter.eidRotateSeconds {
            rotateEid(now: now)
        }
        ackText = ""
        serverOK = nil
        serverDetail = settings.normalizedServer.isEmpty ? "no server address: open Setup" : "connecting"
        BeaconLog.shared.add("SOS started, random id " + eid)

        UIApplication.shared.isIdleTimerDisabled = true
        sensors.start(heading: settings.cameraAssist)
        pucks.start()
        timer = Timer.scheduledTimer(withTimeInterval: Reporter.batchInterval, repeats: true) { [weak self] _ in
            self?.tick()
        }
        tick()
    }

    func stop() {
        if !isSharing {
            return
        }
        sendBye()
        timer?.invalidate()
        timer = nil
        pucks.stop()
        sensors.stop()
        UIApplication.shared.isIdleTimerDisabled = false
        isSharing = false
        ackText = ""
        heardBy = []
        serverOK = nil
        serverDetail = "stopped"
        BeaconLog.shared.add("SOS stopped (sent bye)")
    }

    // MARK: The 0.5 s tick

    private func tick() {
        if !isSharing {
            return
        }
        let now = nowSeconds()
        if now - eidStartedAt > Reporter.eidRotateSeconds {
            rotateEid(now: now)
        }
        if sensors.headingEnabled != settings.cameraAssist {
            sensors.setHeadingEnabled(settings.cameraAssist)
        }
        sensors.refreshBattery()
        let batch = buildBatch(now: now)
        post(batch, isBye: false)
    }

    private func rotateEid(now: TimeInterval) {
        let old = eid
        eid = String(format: "%08x", UInt32.random(in: UInt32.min...UInt32.max))
        eidStartedAt = now
        // prev_eid is sent in the reports until the server has accepted one batch.
        pendingPrevEid = old.isEmpty ? nil : old
        if !old.isEmpty {
            BeaconLog.shared.add("Random id rotated: " + old + " -> " + eid)
        }
    }

    private func deviceInfo() -> WireDevice {
        return WireDevice(eid: eid, platform: "ios", model: PhoneInfo.modelName,
                          chip: chipForWire(), app: Reporter.appVersion)
    }

    private func chipForWire() -> String? {
        if !PhoneCapabilities.uwbDistance {
            return nil
        }
        return PhoneInfo.chipGuess
    }

    func buildBatch(now: TimeInterval) -> IngestBatch {
        var obs: [WireObservation] = []
        let heading = sensors.freshTrueHeading(now: now)

        // pucks.readings() holds the latest distance and signal of every board measured in
        // the last 25 s. They are sent in EVERY batch with the time they were measured
        // (`t`), so a board this phone released (take turns) keeps counting on the server.
        // The same (accessory, t) pair can therefore arrive several times.
        for reading in pucks.readings(now: now) {
            if let d = reading.distance {
                var o = WireObservation(kind: "uwb", t: round3(reading.distanceTime), eid: eid)
                o.accessory = reading.key
                o.d_m = round3(d)
                obs.append(o)
            }
            // Bluetooth signal strength of the board, measured on the phone.
            if let rssi = reading.rssi {
                var o = WireObservation(kind: "sos", t: round3(reading.rssiTime), eid: eid)
                o.accessory = reading.key
                o.rssi = rssi
                obs.append(o)
            }
            // EXPERIMENTAL bearing: phone-to-board direction plus the compass.
            if settings.cameraAssist, let h = heading, let az = reading.azimuthDeg, let source = reading.azimuthSource {
                var angle = az
                if source == "camera-assist+compass" && settings.flipAngleSign {
                    angle = -angle
                }
                var o = WireObservation(kind: "bearing", t: round3(now), eid: eid)
                o.accessory = reading.key
                o.to_puck_compass_deg = round1(normalizeDegrees(h + angle))
                o.sigma_deg = Reporter.bearingSigmaDeg
                o.source = source
                obs.append(o)
            }
        }

        obs.append(reportObservation(now: now))
        return IngestBatch(device: deviceInfo(), obs: obs)
    }

    private func reportObservation(now: TimeInterval) -> WireObservation {
        let choice = occupant.choice
        var r = WireReport(status: choice.wireStatus.rawValue,
                           people: occupant.people,
                           can_move: occupant.canMove,
                           safe_outside: choice.safeOutside,
                           platform: "ios",
                           app: Reporter.appVersion)
        r.battery_pct = sensors.batteryPct
        r.floor_reported = settings.homeFloor > 0 ? settings.homeFloor : nil
        r.baro_hpa = sensors.baroHpa
        r.still = sensors.still
        r.still_s = sensors.stillSeconds
        r.home_suite = settings.homeSuite.isEmpty ? nil : settings.homeSuite
        r.text = occupant.noteForWire
        r.model = PhoneInfo.modelName
        r.chip = chipForWire()
        r.network = sensors.network

        var o = WireObservation(kind: "report", t: round3(now), eid: eid)
        o.prev_eid = pendingPrevEid
        o.report = r
        return o
    }

    private func sendBye() {
        let o = WireObservation(kind: "bye", t: round3(nowSeconds()), eid: eid)
        post(IngestBatch(device: deviceInfo(), obs: [o]), isBye: true)
    }

    // MARK: HTTP

    private func post(_ batch: IngestBatch, isBye: Bool) {
        if let pretty = try? prettyEncoder.encode(batch), let text = String(data: pretty, encoding: .utf8) {
            lastBatchJSON = text
        }
        guard let url = settings.url("/api/ingest") else {
            serverOK = false
            serverDetail = "no server address: open Setup"
            return
        }
        guard let body = try? encoder.encode(batch) else {
            return
        }
        if inFlight >= 2 && !isBye {
            serverDetail = "server slow: batch dropped"
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 3
        request.httpBody = body
        inFlight += 1
        let task = urlSession.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                self?.handleResponse(data: data, response: response, error: error, isBye: isBye)
            }
        }
        task.resume()
    }

    private func handleResponse(data: Data?, response: URLResponse?, error: Error?, isBye: Bool) {
        inFlight = max(0, inFlight - 1)
        if isBye || !isSharing {
            return
        }
        if let error = error {
            serverOK = false
            serverDetail = "server unreachable (" + error.localizedDescription + ")"
            return
        }
        var code = 0
        if let http = response as? HTTPURLResponse {
            code = http.statusCode
        }
        if code < 200 || code >= 300 {
            serverOK = false
            serverDetail = "server answered HTTP \(code)"
            return
        }
        serverOK = true
        serverDetail = "connected"
        batchesAccepted += 1
        pendingPrevEid = nil
        guard let data = data else {
            return
        }
        guard let reply = try? JSONDecoder().decode(IngestResponse.self, from: data) else {
            return
        }
        if let ack = reply.ack {
            ackText = ack.text ?? ""
            heardBy = ack.heard_by ?? []
        }
        unknownAccessories = reply.unknown_accessories ?? []
    }
}

// MARK: - Small GET helper for the setup screen (/healthz, /api/directory)

enum ServerAPI {
    /// Calls `completion` on the main queue with the body, or with a problem text.
    static func get(_ url: URL, completion: @escaping (Data?, String?) -> Void) {
        var request = URLRequest(url: url)
        request.timeoutInterval = 4
        let task = URLSession.shared.dataTask(with: request) { data, response, error in
            var problem: String? = nil
            if let error = error {
                problem = error.localizedDescription
            } else if let http = response as? HTTPURLResponse, http.statusCode < 200 || http.statusCode >= 300 {
                problem = "HTTP \(http.statusCode)"
            }
            let finalProblem = problem
            let body = (finalProblem == nil) ? data : nil
            DispatchQueue.main.async {
                completion(body, finalProblem)
            }
        }
        task.resume()
    }
}
