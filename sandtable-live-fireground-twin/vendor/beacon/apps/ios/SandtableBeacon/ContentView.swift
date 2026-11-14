// SandtableBeacon - ContentView.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// The one screen a frightened person uses: a big "I NEED HELP" button; then the
// status buttons, how many people, can you move, a short note, the rescue beacons that
// hear this phone, the server's answer, and what exactly is being sent.

import SwiftUI
import UIKit

struct ContentView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var occupant: Occupant
    @ObservedObject var pucks: PuckManager
    @ObservedObject var reporter: Reporter
    @ObservedObject var sensors: Sensors

    @State private var showSetup = false
    @State private var confirmStop = false

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    if !settings.configNotice.isEmpty {
                        noticeBanner
                    }
                    if reporter.isSharing {
                        sharingBanner
                        ackCard
                        statusGrid
                        detailsCard
                        beaconsCard
                        phoneCard
                        sentCard
                        stopButton
                    } else {
                        idleIntro
                        helpButton
                        idleNotes
                    }
                    footer
                }
                .padding(16)
            }
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSetup) {
            SetupView(settings: settings, pucks: pucks, reporter: reporter,
                      sensors: sensors, log: BeaconLog.shared)
        }
        .alert("Stop sharing?", isPresented: $confirmStop) {
            Button("Stop sharing", role: .destructive) {
                reporter.stop()
            }
            Button("Keep sharing", role: .cancel) {
            }
        } message: {
            Text("Rescuers will no longer see this phone. Only stop if you are safe.")
        }
    }

    // MARK: Header and footer

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Sandtable Beacon")
                    .font(.system(size: 22, weight: .heavy, design: .rounded))
                    .foregroundColor(Theme.text)
                Text("Fire rescue locator")
                    .font(.subheadline)
                    .foregroundColor(Theme.muted)
            }
            Spacer()
            Button(action: { showSetup = true }) {
                Image(systemName: "gearshape.fill")
                    .font(.title2)
                    .foregroundColor(Theme.muted)
                    .padding(8)
            }
            .accessibilityLabel("Setup")
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Prototype \(Reporter.appVersion). Not a certified emergency service: if you can, also call your local emergency number.")
            Text("No background mode in this version: keep this screen open while sharing.")
        }
        .font(.footnote)
        .foregroundColor(Theme.muted)
        .padding(.top, 8)
    }

    private var noticeBanner: some View {
        Button(action: { settings.configNotice = "" }) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "qrcode")
                Text(settings.configNotice)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                Image(systemName: "xmark")
                    .foregroundColor(Theme.muted)
            }
            .font(.subheadline)
            .foregroundColor(Theme.green)
            .card()
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: Before SOS

    private var idleIntro: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Trapped in a fire?")
                .font(.system(size: 30, weight: .heavy, design: .rounded))
                .foregroundColor(Theme.text)
            Text("Press the button. The rescue beacons the fire crew throws into the building will find this phone and tell them where you are.")
                .font(.body)
                .foregroundColor(Theme.text)
        }
    }

    private var helpButton: some View {
        Button(action: { reporter.start() }) {
            VStack(spacing: 10) {
                Image(systemName: "sos")
                    .font(.system(size: 44, weight: .bold))
                Text("I NEED HELP")
                    .font(.system(size: 38, weight: .black, design: .rounded))
                Text("Starts sharing with the rescue team")
                    .font(.subheadline)
            }
            .foregroundColor(.white)
            .frame(maxWidth: .infinity, minHeight: 220)
            .background(
                LinearGradient(gradient: Gradient(colors: [Theme.help, Theme.helpDark]),
                               startPoint: .top, endPoint: .bottom)
            )
            .cornerRadius(28)
            .shadow(color: Theme.help.opacity(0.45), radius: 18, x: 0, y: 6)
        }
        .buttonStyle(PressScaleStyle())
        .accessibilityLabel("I need help. Starts sharing with the rescue team.")
    }

    private var idleNotes: some View {
        VStack(alignment: .leading, spacing: 10) {
            if settings.normalizedServer.isEmpty {
                Label("No rescue server set. Open Setup (gear, top right) first.", systemImage: "exclamationmark.triangle.fill")
                    .foregroundColor(Theme.amber)
            }
            Label("No name, no account, no phone number is sent. Only a random id that changes every 10 minutes.", systemImage: "lock.fill")
                .foregroundColor(Theme.muted)
            Label("Before an emergency: open Setup once and allow the permissions.", systemImage: "checkmark.shield.fill")
                .foregroundColor(Theme.muted)
        }
        .font(.subheadline)
        .card()
    }

    // MARK: While sharing

    private var sharingBanner: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(Theme.help)
                .frame(width: 14, height: 14)
            VStack(alignment: .leading, spacing: 2) {
                Text("SHARING WITH RESCUERS")
                    .font(.system(size: 17, weight: .heavy, design: .rounded))
                    .foregroundColor(Theme.text)
                Text("Keep this screen open. Do not lock the phone.")
                    .font(.subheadline)
                    .foregroundColor(Theme.text)
            }
            Spacer()
        }
        .padding(14)
        .background(Theme.helpDark)
        .cornerRadius(Theme.corner)
    }

    private var ackHeadline: String {
        if !reporter.ackText.isEmpty {
            return reporter.ackText
        }
        if reporter.serverOK == false {
            return "Server unreachable. Still trying."
        }
        if pucks.rows.isEmpty {
            return "Looking for rescue beacons..."
        }
        return "Waiting for the rescue team's answer..."
    }

    private var ackColor: Color {
        if reporter.serverOK == false {
            return Theme.amber
        }
        if !reporter.ackText.isEmpty {
            return Theme.green
        }
        return Theme.text
    }

    private var ackCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(ackHeadline)
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .foregroundColor(ackColor)
                .fixedSize(horizontal: false, vertical: true)
            Text("Server: " + reporter.serverDetail)
                .font(.footnote)
                .foregroundColor(Theme.muted)
        }
        .card()
    }

    private var statusGrid: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionTitle(text: "What is your situation?")
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                ForEach(StatusChoice.allCases) { choice in
                    statusButton(choice)
                }
            }
        }
    }

    private func statusButton(_ choice: StatusChoice) -> some View {
        let selected = occupant.choice == choice
        return Button(action: { occupant.choice = choice }) {
            VStack(spacing: 4) {
                Image(systemName: choice.systemImage)
                    .font(.title2)
                Text(choice.title)
                    .font(.system(size: 19, weight: .bold, design: .rounded))
                Text(choice.subtitle)
                    .font(.caption)
                    .multilineTextAlignment(.center)
            }
            .foregroundColor(selected ? Color.black : Theme.text)
            .padding(.vertical, 12)
            .padding(.horizontal, 6)
            .frame(maxWidth: .infinity, minHeight: 104)
            .background(selected ? choice.color : Theme.card)
            .cornerRadius(Theme.corner)
            .overlay(
                RoundedRectangle(cornerRadius: Theme.corner)
                    .stroke(choice.color, lineWidth: selected ? 0 : 2)
            )
        }
        .buttonStyle(PressScaleStyle())
        .accessibilityLabel(choice.title + (selected ? ", selected" : ""))
    }

    private var detailsCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Stepper(value: $occupant.people, in: 1...30) {
                Text("People with me: \(occupant.people)")
                    .font(.headline)
                    .foregroundColor(Theme.text)
            }
            Toggle(isOn: $occupant.canMove) {
                Text("I can move")
                    .font(.headline)
                    .foregroundColor(Theme.text)
            }
            .tint(Theme.green)
            TextField("Short note (optional), e.g. back office, smoke under door", text: $occupant.note)
                .textFieldStyle(RoundedBorderTextFieldStyle())
                .submitLabel(.done)
            Text("\(occupant.note.count)/120 characters")
                .font(.caption)
                .foregroundColor(occupant.note.count > 120 ? Theme.amber : Theme.muted)
        }
        .card()
    }

    private var beaconsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Rescue beacons")
            if settings.takeTurns {
                Text("Taking turns: one beacon at a time, \(settings.holdSeconds) s each, so another phone can use them too.")
                    .font(.footnote)
                    .foregroundColor(Theme.blue)
            }
            if pucks.rows.isEmpty {
                Text("No rescue beacon heard yet. " + pucks.bluetoothText + ".")
                    .font(.subheadline)
                    .foregroundColor(Theme.muted)
            }
            ForEach(pucks.rows) { row in
                BeaconRowView(row: row, onToggle: { on in
                    pucks.setBoardEnabled(row.id, on)
                })
            }
            if pucks.nearbyPermissionDenied {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Nearby Interaction is off for this app, so no distance can be measured. Turn it on in Settings > Privacy & Security > Nearby Interactions.")
                        .font(.subheadline)
                        .foregroundColor(Theme.amber)
                    Button("Open Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                }
            }
            if let warning = pucks.warning {
                Text(warning)
                    .font(.footnote)
                    .foregroundColor(Theme.amber)
            }
            if !reporter.unknownAccessories.isEmpty {
                Text("Not yet placed by the rescue team: " + reporter.unknownAccessories.joined(separator: ", "))
                    .font(.footnote)
                    .foregroundColor(Theme.muted)
            }
        }
        .card()
    }

    private var phoneCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionTitle(text: "This phone")
            HStack(spacing: 16) {
                Label(batteryText, systemImage: "battery.50")
                Label(stillText, systemImage: "figure.stand")
            }
            .font(.subheadline)
            .foregroundColor(Theme.text)
            if let hpa = sensors.baroHpa {
                Text(String(format: "Air pressure %.1f hPa (helps the team tell your floor)", hpa))
                    .font(.footnote)
                    .foregroundColor(Theme.muted)
            }
            if settings.cameraAssist {
                Text("Experimental direction: hold the phone up, camera unobstructed, and move it slowly. Compass: " + sensors.headingText)
                    .font(.footnote)
                    .foregroundColor(Theme.blue)
            }
        }
        .card()
    }

    private var batteryText: String {
        if let pct = sensors.batteryPct {
            return "Battery \(pct) %"
        }
        return "Battery unknown"
    }

    private var stillText: String {
        guard let still = sensors.still else {
            return "Motion unknown"
        }
        if still {
            let seconds = Int(sensors.stillSeconds ?? 0)
            return "Still for \(seconds) s"
        }
        return "Moving"
    }

    private var sentCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Random id: " + reporter.eid + " (changes every 10 minutes). No name, no account, no phone number.")
                .font(.footnote)
                .foregroundColor(Theme.muted)
            DisclosureGroup("What is being sent") {
                ScrollView(.horizontal) {
                    Text(reporter.lastBatchJSON)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(Theme.text)
                        .textSelection(.enabled)
                        .padding(.top, 6)
                }
            }
            .foregroundColor(Theme.text)
            .accentColor(Theme.muted)
        }
        .card()
    }

    private var stopButton: some View {
        Button(action: { confirmStop = true }) {
            Text("Stop sharing")
                .font(.system(size: 18, weight: .bold, design: .rounded))
                .foregroundColor(Theme.text)
                .frame(maxWidth: .infinity, minHeight: 54)
                .background(Theme.card)
                .cornerRadius(Theme.corner)
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.corner)
                        .stroke(Theme.muted, lineWidth: 1)
                )
        }
        .buttonStyle(PressScaleStyle())
    }
}

/// One rescue beacon: its key (for labelling the board), state, Bluetooth signal, the
/// latest UWB distance (live, or how old it is), and this phone's "use" toggle.
struct BeaconRowView: View {
    let row: PuckRow
    let onToggle: (Bool) -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(row.title)
                    .font(.system(size: 15, weight: .semibold, design: .monospaced))
                    .foregroundColor(row.enabled ? Theme.text : Theme.muted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text(stateText)
                    .font(.caption)
                    .foregroundColor(row.connected ? Theme.green : Theme.muted)
                    .lineLimit(2)
                Text(signalText)
                    .font(.caption)
                    .foregroundColor(Theme.muted)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(distanceText)
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundColor(row.distance == nil ? Theme.muted : Theme.text)
                    .monospacedDigit()
                Text(ageText)
                    .font(.caption2)
                    .foregroundColor(Theme.muted)
                HStack(spacing: 4) {
                    Text("use")
                        .font(.caption2)
                        .foregroundColor(Theme.muted)
                    Toggle("Use this board", isOn: useBinding)
                        .labelsHidden()
                        .tint(Theme.green)
                }
            }
        }
        .padding(10)
        .background(Theme.background)
        .cornerRadius(10)
    }

    private var useBinding: Binding<Bool> {
        return Binding<Bool>(
            get: { row.enabled },
            set: { on in onToggle(on) }
        )
    }

    private var stateText: String {
        var text = row.state
        if row.cameraAssist {
            text += " · camera"
        }
        return text
    }

    private var signalText: String {
        if let rssi = row.rssi {
            return "Bluetooth \(rssi) dBm"
        }
        return "Bluetooth signal: reading..."
    }

    private var distanceText: String {
        guard let d = row.distance else {
            return "-- m"
        }
        return String(format: "%.2f m", d)
    }

    private var ageText: String {
        guard let age = row.distanceAge, row.distance != nil else {
            return " "
        }
        if age < 1.5 {
            return "live"
        }
        return "\(Int(age)) s ago"
    }
}
