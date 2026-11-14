// SandtableBeacon - SetupView.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// Setup: the Beacon server's address, the suite pick-list (GET /api/directory),
// how many boards to use, a "find beacons now" test that sends nothing (to read and
// label each board's key), the experimental camera-assisted direction, what this
// phone's Nearby Interaction hardware supports, and the event log.

import SwiftUI

struct SetupView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var pucks: PuckManager
    @ObservedObject var reporter: Reporter
    @ObservedObject var sensors: Sensors
    @ObservedObject var log: BeaconLog

    @Environment(\.dismiss) private var dismiss

    @State private var serverCheck: String = ""
    @State private var directory: [DirectoryEntry] = []
    @State private var directoryStatus: String = ""

    var body: some View {
        NavigationView {
            Form {
                serverSection
                suiteSection
                beaconsSection
                experimentalSection
                phoneSection
                privacySection
                logSection
            }
            .navigationTitle("Setup")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: Server

    private var serverSection: some View {
        Section(header: Text("Rescue server"),
                footer: Text("Fastest: scan the QR code on the board page with the iPhone's Camera app. Or type the address the launcher (python -m beacon.demo --source live --lan) prints, port 1885. Use the IP address.")) {
            TextField("http://192.168.1.20:1885", text: $settings.serverAddress)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .disableAutocorrection(true)
            if let warning = settings.atsWarning {
                Text(warning)
                    .font(.footnote)
                    .foregroundColor(.orange)
            }
            Button("Test connection") {
                testServer()
            }
            if !serverCheck.isEmpty {
                Text(serverCheck)
                    .font(.footnote)
            }
        }
    }

    private func testServer() {
        guard let url = settings.url("/healthz") else {
            serverCheck = "Type the server address first."
            return
        }
        serverCheck = "Checking " + url.absoluteString + " ..."
        ServerAPI.get(url) { data, problem in
            if let problem = problem {
                serverCheck = "Not reachable: " + problem + ". Check the address, the Wi-Fi, the Local Network permission (Settings > Privacy & Security > Local Network) and the laptop's firewall."
                return
            }
            var text = "Server reachable."
            if let data = data, let body = String(data: data, encoding: .utf8) {
                text += " " + String(body.prefix(160))
            }
            serverCheck = text
        }
    }

    // MARK: Suite

    private var suiteSection: some View {
        Section(header: Text("Which suite are you in?"),
                footer: Text("Optional. Tells the rescue team where you say you are, and lets them compare the building's tenant list with the phones still inside. Floor 0 = do not send a floor.")) {
            Button("Load suites from server") {
                loadDirectory()
            }
            if !directoryStatus.isEmpty {
                Text(directoryStatus)
                    .font(.footnote)
            }
            ForEach(directory) { entry in
                Button(action: {
                    settings.homeSuite = entry.suite
                    if let n = entry.floorNumber {
                        settings.homeFloor = n
                    }
                }) {
                    HStack {
                        Text(entry.displayName)
                        Spacer()
                        if settings.homeSuite == entry.suite {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
            HStack {
                Text(suiteLine)
                Spacer()
                if !settings.homeSuite.isEmpty {
                    Button("Clear") {
                        settings.homeSuite = ""
                    }
                }
            }
            Stepper(value: $settings.homeFloor, in: 0...150) {
                Text(floorLine)
            }
        }
    }

    private func loadDirectory() {
        guard let url = settings.url("/api/directory") else {
            directoryStatus = "Type the server address first."
            return
        }
        directoryStatus = "Loading..."
        ServerAPI.get(url) { data, problem in
            if let problem = problem {
                directoryStatus = "Could not load: " + problem
                return
            }
            guard let data = data, let entries = DirectoryParser.parse(data) else {
                directoryStatus = "The server's suite list was not in the expected format."
                return
            }
            directory = entries
            directoryStatus = "\(entries.count) suites. Tap yours."
        }
    }

    // MARK: Beacons

    private var testBinding: Binding<Bool> {
        return Binding<Bool>(
            get: { pucks.isRunning },
            set: { on in
                if on {
                    pucks.start()
                } else {
                    pucks.stop()
                }
            }
        )
    }

    private var beaconsSection: some View {
        Section(header: Text("Rescue beacons"),
                footer: Text("A board serves ONE phone at a time (Qorvo's stock firmware) and stops advertising while connected. Two phones sharing boards: turn on Take turns on BOTH phones, or switch boards off per phone with the use toggles. The key (name#ABCD) is what the server sees: write it on the board.")) {
            Stepper(value: $settings.maxPucks, in: 1...5) {
                Text("Use at most \(settings.maxPucks) boards at once")
            }
            .disabled(settings.takeTurns)
            Toggle("Take turns (one board at a time)", isOn: $settings.takeTurns)
            if settings.takeTurns {
                Stepper(value: $settings.holdSeconds, in: 2...20) {
                    Text("Hold each board \(settings.holdSeconds) s")
                }
            }
            Toggle("Find beacons now (sends nothing)", isOn: testBinding)
                .disabled(reporter.isSharing)
            Text(pucks.bluetoothText + ". Connected: \(pucks.connectedCount)")
                .font(.footnote)
            ForEach(pucks.rows) { row in
                BeaconRowView(row: row, onToggle: { on in
                    pucks.setBoardEnabled(row.id, on)
                })
                .listRowBackground(Theme.card)
            }
            if let problem = pucks.lastProblem {
                Text("Last problem: " + problem)
                    .font(.footnote)
                    .foregroundColor(.orange)
            }
            Toggle("Key = board name only (no #ABCD)", isOn: $settings.nameOnlyKey)
        }
    }

    // MARK: Experimental

    private var experimentalSection: some View {
        Section(header: Text("Experimental: camera-assisted direction"),
                footer: Text("Off by default. On iPhone 14 and 15 Nearby Interaction gives no direction to these boards; camera assistance can add an angle, but only with light, an unobstructed camera and a moving phone, so it does not work in smoke or in a pocket. Uses the camera and location (for true north). Applies to boards connected after you turn it on, one board at a time.")) {
            Toggle("Camera-assisted direction", isOn: $settings.cameraAssist)
            if settings.cameraAssist {
                Toggle("Flip angle sign (if bearings point the wrong way)", isOn: $settings.flipAngleSign)
                Text("Compass: " + sensors.headingText)
                    .font(.footnote)
            }
        }
    }

    // MARK: Phone

    private var phoneSection: some View {
        Section(header: Text("This phone")) {
            Text(PhoneInfo.modelName + "  (" + PhoneInfo.identifier + ")")
            Text(chipLine)
            Text(PhoneCapabilities.line())
                .font(.footnote)
            Text(eidLine)
                .font(.footnote)
        }
    }

    private var privacySection: some View {
        Section(header: Text("Privacy")) {
            Text("Sent only while you share: your status, how many people are with you, whether you can move, your note, battery, air pressure, whether the phone is still, your suite and floor if you set them, the phone model, and the distance and signal to each rescue beacon. Never sent: your name, number, account, contacts or GPS position. The id is random and changes every 10 minutes.")
                .font(.footnote)
        }
    }

    // MARK: Event log

    private var logSection: some View {
        Section(header: Text("Event log (newest first)")) {
            if log.lines.isEmpty {
                Text("Nothing yet.")
                    .font(.footnote)
            }
            ForEach(0..<min(log.lines.count, 60), id: \.self) { i in
                Text(log.lines[log.lines.count - 1 - i])
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
    }
    // MARK: Plain-String helpers (kept out of Text(...) so the type checker has one choice)

    private var suiteLine: String {
        if settings.homeSuite.isEmpty {
            return "Suite: not set"
        }
        return "Suite: " + settings.homeSuite
    }

    private var floorLine: String {
        if settings.homeFloor == 0 {
            return "Floor: not sent"
        }
        return "Floor: " + String(settings.homeFloor)
    }

    private var chipLine: String {
        if !PhoneCapabilities.uwbDistance {
            return "UWB chip: none (Bluetooth signal only)"
        }
        return "UWB chip: " + (PhoneInfo.chipGuess ?? "yes")
    }

    private var eidLine: String {
        if reporter.eid.isEmpty {
            return "Random id now: (made when you press I NEED HELP)"
        }
        return "Random id now: " + reporter.eid
    }
}
