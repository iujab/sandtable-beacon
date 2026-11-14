// SandtableBeacon - Theme.swift
//
// UNCOMPILED SOURCE. Written on a machine without a Mac or a Swift compiler.
// It was parsed for syntax with tree-sitter only (apps/ios/tools/syntax_check.py);
// types and API names have NOT been checked by a compiler. See docs/IOS_BUILD.md.
//
// Colours and small reusable view pieces. The app is always dark: high contrast,
// big type, readable in smoke and at low screen brightness.

import SwiftUI

enum Theme {
    static let background = Color(red: 0.043, green: 0.051, blue: 0.063)
    static let card = Color(red: 0.090, green: 0.102, blue: 0.122)
    static let cardBorder = Color(red: 0.150, green: 0.165, blue: 0.192)
    static let text = Color(red: 0.957, green: 0.961, blue: 0.969)
    static let muted = Color(red: 0.604, green: 0.627, blue: 0.659)

    static let help = Color(red: 0.898, green: 0.216, blue: 0.169)
    static let helpDark = Color(red: 0.620, green: 0.110, blue: 0.090)
    static let orange = Color(red: 0.949, green: 0.443, blue: 0.110)
    static let amber = Color(red: 0.961, green: 0.647, blue: 0.141)
    static let green = Color(red: 0.184, green: 0.749, blue: 0.443)
    static let blue = Color(red: 0.306, green: 0.659, blue: 0.871)

    static let corner: CGFloat = 14
}

extension StatusChoice {
    var color: Color {
        switch self {
        case .trapped:
            return Theme.help
        case .injured:
            return Theme.orange
        case .sheltering:
            return Theme.amber
        case .out:
            return Theme.green
        }
    }
}

/// A dark rounded card behind a group of controls.
struct CardBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card)
            .cornerRadius(Theme.corner)
            .overlay(
                RoundedRectangle(cornerRadius: Theme.corner)
                    .stroke(Theme.cardBorder, lineWidth: 1)
            )
    }
}

extension View {
    func card() -> some View {
        return modifier(CardBackground())
    }
}

/// Shrinks a little while pressed, so a frightened thumb gets feedback.
struct PressScaleStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .opacity(configuration.isPressed ? 0.85 : 1.0)
    }
}

/// Small uppercase section title.
struct SectionTitle: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 13, weight: .bold, design: .rounded))
            .foregroundColor(Theme.muted)
            .tracking(1.2)
    }
}
