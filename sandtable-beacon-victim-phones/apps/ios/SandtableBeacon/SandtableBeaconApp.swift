// SandtableBeacon - SandtableBeaconApp.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// The occupant app of Sandtable Beacon. A person trapped in a fire opens it and
// presses "I NEED HELP"; the app ranges every rescue board (Qorvo DWM3001CDK, stock
// Nearby Interaction firmware) it can hear over UWB, one NISession per board, and posts
// the distances and a status to the Beacon server, which locates the phone.
//
// Swift 5 language mode, iOS 16.0, iPhone only, no third-party packages, no
// background modes (the person keeps the screen open).

import SwiftUI

/// Owns the long-lived objects. Everything runs on the main queue.
final class AppModel: ObservableObject {
    let settings: AppSettings
    let occupant: Occupant
    let sensors: Sensors
    let pucks: PuckManager
    let reporter: Reporter

    init() {
        let s = AppSettings()
        let o = Occupant()
        let se = Sensors()
        let p = PuckManager(settings: s)
        self.settings = s
        self.occupant = o
        self.sensors = se
        self.pucks = p
        self.reporter = Reporter(settings: s, pucks: p, sensors: se, occupant: o)
    }
}

@main
struct SandtableBeaconApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView(settings: model.settings,
                        occupant: model.occupant,
                        pucks: model.pucks,
                        reporter: model.reporter,
                        sensors: model.sensors)
                .onOpenURL { url in
                    // sandtablebeacon://server?url=http://HOST:1885 (QR code on the board page)
                    _ = model.settings.applyConfigURL(url)
                }
        }
    }
}
