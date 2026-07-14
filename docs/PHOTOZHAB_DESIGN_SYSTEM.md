# Photozhab web design system

Source of truth: the final `*-b-v2.dc.html` screens from the operator-supplied
Claude Design archive, adapted to production HTML in `deploy/photozhab/`.
Claude runtime elements, mock balances/users and placeholder media are not part
of the production system.

## Tokens

- Surfaces: `#0b0d0c` page, `#0e100f` alternate, `#121512` card,
  `#f2efe4` primary text.
- Accents: `#d3f36b` primary lime, `#7fd8d7` cyan, `#ff9a8c` coral.
- Type: Space Grotesk 500/600/700 for display, Inter 400/500/600/700 for UI,
  JetBrains Mono 400/600/700 for prices, labels and metadata.
- Spacing: the production scale is 4, 8, 12, 16, 20, 24, 32, 40, 48, 64,
  80 and 96 px. Responsive section padding uses `clamp(64px, 9vw, 120px)`.
- Radius: 4, 6, 8, 12, 18/20 and 999 px. Primary actions are pills; content
  panels use 14-20 px.
- Motion: 160-260 ms for controls, 700 ms for scroll reveals. All continuous
  motion has a `prefers-reduced-motion` fallback.
- Layout: public content 1160 px, app composer 820 px, app conversation 780 px,
  app sidebar 264 px and admin sidebar 228 px.

The executable token definitions remain in `deploy/photozhab/styles.css`; page
styles consume those variables instead of creating a second runtime token file.

## Component inventory

### Brand mark

Frog SVG on a lime rounded square/circle. Used in public header, app and admin.
Decorative instances are hidden from assistive technology. Do not replace it
with an emoji or a text `P`.

### Button

Variants: lime primary, outlined secondary, ghost and destructive. Sizes are
36, 42, 46 and 54 px. Every button has hover, active, focus-visible, disabled
and, for async actions, loading/disabled feedback. Use one primary action per
surface; do not place two equal lime actions next to each other.

### Card and panel

Variants: neutral surface, lime-accented, coral error and glass composer/dialog.
All use a complete thin border; left-border-only cards are reserved for no
default state. Cards compose headings, metadata, media and actions.

### Form field

Label + semantic input/select/textarea + optional helper/error. Labels use mono
uppercase; focus uses the cyan/lime ring. Placeholder text never replaces the
accessible label. Disabled fields remain readable and explain why they cannot
be used.

### Public organisms

Sticky header, hero with five real-media variants, generation demo, trust row,
feature grid, real showcase, step grid, price table, messenger phone, FAQ, CTA,
mobile sticky CTA and legal footer. The hero experiment changes only media;
copy, controls and analytics contract remain shared.

### Generation app organisms

Mode sidebar, session history, gallery, conversation messages, generation
progress, refunded-error panel, expanded composer, authentication dialog,
payment selector and toast. Gallery entries are real results from the current
tab only; persistent/cross-device history must not be implied until a backend
contract exists.

### Admin organisms

228 px navigation, page header, KPI grid, live-data tables, editors, account
onboarding, support cockpit, user/seller/referral views, analytics and ad
calculator. All existing IDs and tab keys are API contracts. Never substitute
example figures from the design export for live values.

## Known production adaptations

- Real Photozhab WebP/MP4 assets replace every Claude image slot.
- The honest public promise is `Идея → картинка за 1 минуту`; subordinate copy
  uses the same expectation instead of the mock's 9-12 second claim.
- Responsive layouts, semantic HTML, keyboard focus, reduced motion and real
  auth/payment/generation behavior take precedence over mock-only interactions.
