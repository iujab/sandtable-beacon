// SandtableBeacon - Sensors.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// Everything here is optional and failure-tolerant: a sensor that is missing, denied or
// broken just leaves its value nil, and nil values are left out of the report.
//   - battery: UIDevice battery monitoring (no permission)
//   - stillness: CMMotionManager accelerometer (no permission)
//   - barometric pressure: CMAltimeter (asks for Motion & Fitness permission)
//   - network type: NWPathMonitor (no permission)
//   - compass heading: CLLocationManager, ONLY when the experimental camera-assisted
//     direction toggle is on (asks for location "While Using"; true north needs it)
//
// Threading: every handler is delivered on the main queue.

import Foundation
import Combine
import UIKit
import CoreMotion
import CoreLocation
import Network

final class Sensors: NSObject, ObservableObject {
    @Published private(set) var batteryPct: Int?
    @Published private(set) var baroHpa: Double?
    @Published private(set) var still: Bool?
    @Published private(set) var stillSeconds: Double?
    @Published private(set) var network: String?
    @Published private(set) var trueHeading: Double?
    @Published private(set) var headingText: String = "off"

    private(set) var headingEnabled = false
    private(set) var headingTime: TimeInterval = 0

    private let motion = CMMotionManager()
    private let altimeter = CMAltimeter()
    private var locationManager: CLLocationManager?
    private var pathMonitor: NWPathMonitor?
    private var smoothedMagnitude: Double?
    private var lastMovement: TimeInterval = 0
    private var started = false

    /// Seconds without movement before the phone counts as "still".
    private let stillAfter: TimeInterval = 5
    /// Change in total acceleration (in g) that counts as movement.
    private let movementThreshold: Double = 0.03

    // MARK: Start and stop

    func start(heading: Bool) {
        if !started {
            started = true
            refreshBattery()
            startMotion()
            startAltimeter()
            startNetworkMonitor()
        }
        setHeadingEnabled(heading)
    }

    func stop() {
        if started {
            started = false
            if motion.isAccelerometerActive {
                motion.stopAccelerometerUpdates()
            }
            altimeter.stopRelativeAltitudeUpdates()
            pathMonitor?.cancel()
            pathMonitor = nil
        }
        setHeadingEnabled(false)
    }

    // MARK: Battery

    func refreshBattery() {
        let device = UIDevice.current
        if !device.isBatteryMonitoringEnabled {
            device.isBatteryMonitoringEnabled = true
        }
        let level = device.batteryLevel
        if level < 0 {
            batteryPct = nil
        } else {
            batteryPct = Int((level * 100).rounded())
        }
    }

    // MARK: Stillness (accelerometer)

    private func startMotion() {
        guard motion.isAccelerometerAvailable else {
            return
        }
        lastMovement = nowSeconds()
        motion.accelerometerUpdateInterval = 0.1
        motion.startAccelerometerUpdates(to: OperationQueue.main) { [weak self] data, _ in
            guard let self = self, let a = data?.acceleration else {
                return
            }
            self.handleAcceleration(x: a.x, y: a.y, z: a.z)
        }
    }

    private func handleAcceleration(x: Double, y: Double, z: Double) {
        let magnitude = (x * x + y * y + z * z).squareRoot()
        let now = nowSeconds()
        if let smoothed = smoothedMagnitude {
            if abs(magnitude - smoothed) > movementThreshold {
                lastMovement = now
            }
            smoothedMagnitude = 0.8 * smoothed + 0.2 * magnitude
        } else {
            smoothedMagnitude = magnitude
        }
        let quiet = now - lastMovement
        let isStill = quiet >= stillAfter
        if still != isStill {
            still = isStill
        }
        let seconds = isStill ? quiet.rounded() : 0
        if stillSeconds != seconds {
            stillSeconds = seconds
        }
    }

    // MARK: Barometer

    private func startAltimeter() {
        guard CMAltimeter.isRelativeAltitudeAvailable() else {
            return
        }
        altimeter.startRelativeAltitudeUpdates(to: OperationQueue.main) { [weak self] data, _ in
            guard let self = self, let data = data else {
                return
            }
            // CMAltitudeData.pressure is in kilopascals; 1 kPa = 10 hPa.
            let hpa = data.pressure.doubleValue * 10.0
            self.baroHpa = (hpa * 100.0).rounded() / 100.0
        }
    }

    // MARK: Network type

    private func startNetworkMonitor() {
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            var kind: String? = nil
            if path.status == .satisfied {
                if path.usesInterfaceType(.wifi) {
                    kind = "wifi"
                } else if path.usesInterfaceType(.cellular) {
                    kind = "cell"
                }
            }
            let result = kind
            DispatchQueue.main.async {
                self?.network = result
            }
        }
        monitor.start(queue: DispatchQueue.global(qos: .utility))
        pathMonitor = monitor
    }

    // MARK: Compass (only for the experimental camera-assisted direction)

    func setHeadingEnabled(_ on: Bool) {
        if on == headingEnabled {
            return
        }
        headingEnabled = on
        if on {
            guard CLLocationManager.headingAvailable() else {
                headingText = "no compass on this device"
                return
            }
            if locationManager == nil {
                let manager = CLLocationManager()
                manager.delegate = self
                manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
                manager.headingFilter = 2
                locationManager = manager
            }
            guard let manager = locationManager else {
                return
            }
            if manager.authorizationStatus == .notDetermined {
                manager.requestWhenInUseAuthorization()
            }
            // True north needs location updates running as well (CLHeading.trueHeading).
            manager.startUpdatingLocation()
            manager.startUpdatingHeading()
            headingText = "starting"
        } else {
            locationManager?.stopUpdatingHeading()
            locationManager?.stopUpdatingLocation()
            trueHeading = nil
            headingText = "off"
        }
    }

    /// The true heading if it is less than 2 s old.
    func freshTrueHeading(now: TimeInterval) -> Double? {
        guard headingEnabled, let h = trueHeading, now - headingTime < 2.0 else {
            return nil
        }
        return h
    }
}

extension Sensors: CLLocationManagerDelegate {

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        if newHeading.trueHeading >= 0 {
            trueHeading = newHeading.trueHeading
            headingTime = nowSeconds()
            headingText = String(format: "%.0f deg true (accuracy %.0f deg)",
                                 newHeading.trueHeading, newHeading.headingAccuracy)
        } else {
            trueHeading = nil
            headingText = "magnetic only: allow location for true north"
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        // Not used: location updates only run so that the heading is relative to true north.
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        headingText = "location error: " + error.localizedDescription
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        if status == .denied || status == .restricted {
            headingText = "location denied: Settings > Privacy & Security > Location Services"
        } else if headingEnabled {
            manager.startUpdatingLocation()
            manager.startUpdatingHeading()
        }
    }
}
