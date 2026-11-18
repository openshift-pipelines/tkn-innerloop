#!/usr/bin/env python3
import xml.etree.ElementTree as ET
from pathlib import Path

ART_DIR = Path(__file__).resolve().parent

SANS = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif"
MONO = "ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace"

C = {
    "bg1": "#24283b",
    "bg2": "#1f2335",
    "panel": "#1a1b26",
    "panel2": "#16161e",
    "border": "#414868",
    "text": "#c0caf5",
    "dim": "#7f88b3",
    "violet": "#bb9af7",
    "blue": "#7aa2f7",
    "green": "#9ece6a",
    "red": "#f7768e",
    "amber": "#e0af68",
    "cyan": "#7dcfff",
}


def wrap_svg(defs_extra: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 540" width="720" height="540" fill="none">
  <defs>
    <linearGradient id="bgGrad" x1="0" y1="0" x2="720" y2="540" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="{C['bg1']}"/>
      <stop offset="100%" stop-color="{C['bg2']}"/>
    </linearGradient>
    <radialGradient id="glowViolet" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{C['violet']}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="{C['violet']}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowBlue" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{C['blue']}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="{C['blue']}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowGreen" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{C['green']}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="{C['green']}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowRed" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{C['red']}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="{C['red']}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowAmber" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{C['amber']}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="{C['amber']}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowCyan" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{C['cyan']}" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="{C['cyan']}" stop-opacity="0"/>
    </radialGradient>
{defs_extra}  </defs>
  <rect x="4" y="4" width="712" height="532" rx="28" fill="url(#bgGrad)" stroke="{C['border']}" stroke-width="2"/>
{body}
</svg>
"""


def icon_laptop(cx: float, cy: float, w: float = 200, h: float = 136, stroke: str = C["blue"], fill: str = C["panel"]) -> str:
    x = cx - w / 2
    y = cy - h / 2
    base_w = w * 1.24
    base_x = cx - base_w / 2
    base_y = y + h + 4
    return f"""
  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="5"/>
  <path d="M {base_x:.1f} {base_y:.1f} H {base_x + base_w:.1f} L {base_x + base_w - 18:.1f} {base_y + 18:.1f} H {base_x + 18:.1f} Z" fill="{C['bg1']}" stroke="{stroke}" stroke-width="5" stroke-linejoin="round"/>
"""


def icon_cloud(cx: float, cy: float, scale: float = 1.0, stroke: str = C["dim"], fill: str = C["panel"], opacity: float = 1.0, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="10 8"' if dashed else ""
    return f"""
  <g transform="translate({cx:.1f},{cy:.1f}) scale({scale:.2f})" opacity="{opacity}">
    <path d="M -72 34 H 68 A 34 34 0 0 0 74 -32 A 48 48 0 0 0 -14 -54 A 42 42 0 0 0 -76 -12 A 32 32 0 0 0 -72 34 Z" fill="{fill}" stroke="{stroke}" stroke-width="5" stroke-linejoin="round"{dash}/>
  </g>
"""


def icon_container(x: float, y: float, w: float, h: float, stroke: str = C["cyan"], fill: str = C["panel"]) -> str:
    ribs = []
    rib_count = 5
    step = (w - 64) / (rib_count - 1)
    for i in range(rib_count):
        rx = x + 32 + i * step
        ribs.append(f'<line x1="{rx:.1f}" y1="{y + 24:.1f}" x2="{rx:.1f}" y2="{y + h - 24:.1f}" stroke="{stroke}" stroke-width="4" stroke-linecap="round" opacity="0.28"/>')
    ribs_str = "\n  ".join(ribs)
    return f"""
  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="22" fill="{fill}" stroke="{stroke}" stroke-width="5"/>
  <rect x="{x + 12:.1f}" y="{y + 12:.1f}" width="{w - 24:.1f}" height="{h - 24:.1f}" rx="14" fill="none" stroke="{stroke}" stroke-width="2" opacity="0.35"/>
  {ribs_str}
"""


def icon_document(x: float, y: float, w: float, h: float, stroke: str = C["violet"], fill: str = C["panel"], fold: float = 34) -> str:
    return f"""
  <path d="M {x:.1f} {y + 16:.1f} A 16 16 0 0 1 {x + 16:.1f} {y:.1f} H {x + w - fold:.1f} L {x + w:.1f} {y + fold:.1f} V {y + h - 16:.1f} A 16 16 0 0 1 {x + w - 16:.1f} {y + h:.1f} H {x + 16:.1f} A 16 16 0 0 1 {x:.1f} {y + h - 16:.1f} Z" fill="{fill}" stroke="{stroke}" stroke-width="5" stroke-linejoin="round"/>
  <path d="M {x + w - fold:.1f} {y:.1f} V {y + fold - 8:.1f} A 8 8 0 0 0 {x + w - fold + 8:.1f} {y + fold:.1f} H {x + w:.1f}" fill="none" stroke="{stroke}" stroke-width="4" stroke-linejoin="round"/>
"""


def icon_agent(cx: float, cy: float, size: float = 110, stroke: str = C["violet"], fill: str = C["panel"]) -> str:
    half = size / 2
    x = cx - half
    y = cy - half
    return f"""
  <line x1="{cx:.1f}" y1="{y - 26:.1f}" x2="{cx:.1f}" y2="{y:.1f}" stroke="{stroke}" stroke-width="5" stroke-linecap="round"/>
  <circle cx="{cx:.1f}" cy="{y - 32:.1f}" r="9" fill="{stroke}"/>
  <rect x="{x - 10:.1f}" y="{cy - 16:.1f}" width="10" height="32" rx="5" fill="{stroke}"/>
  <rect x="{x + size:.1f}" y="{cy - 16:.1f}" width="10" height="32" rx="5" fill="{stroke}"/>
  <rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" rx="26" fill="{fill}" stroke="{stroke}" stroke-width="5"/>
  <circle cx="{cx - 22:.1f}" cy="{cy - 8:.1f}" r="9" fill="{C['cyan']}"/>
  <circle cx="{cx + 22:.1f}" cy="{cy - 8:.1f}" r="9" fill="{C['cyan']}"/>
  <path d="M {cx - 20:.1f} {cy + 22:.1f} H {cx + 20:.1f}" stroke="{stroke}" stroke-width="5" stroke-linecap="round"/>
"""


def badge_check(cx: float, cy: float, r: float = 30) -> str:
    return f"""
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{C['panel']}" stroke="{C['green']}" stroke-width="5"/>
  <path d="M {cx - r * 0.42:.1f} {cy + 1:.1f} L {cx - r * 0.1:.1f} {cy + r * 0.34:.1f} L {cx + r * 0.46:.1f} {cy - r * 0.3:.1f}" fill="none" stroke="{C['green']}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
"""


def badge_cross(cx: float, cy: float, r: float = 30) -> str:
    d = r * 0.36
    return f"""
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{C['panel']}" stroke="{C['red']}" stroke-width="5"/>
  <path d="M {cx - d:.1f} {cy - d:.1f} L {cx + d:.1f} {cy + d:.1f} M {cx + d:.1f} {cy - d:.1f} L {cx - d:.1f} {cy + d:.1f}" fill="none" stroke="{C['red']}" stroke-width="6" stroke-linecap="round"/>
"""


def badge_question(cx: float, cy: float, r: float = 30) -> str:
    return f"""
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{C['panel']}" stroke="{C['amber']}" stroke-width="5"/>
  <path d="M {cx - 10:.1f} {cy - 10:.1f} A 11 11 0 1 1 {cx + 6:.1f} {cy + 2:.1f} C {cx + 1:.1f} {cy + 5:.1f} {cx:.1f} {cy + 8:.1f} {cx:.1f} {cy + 13:.1f}" fill="none" stroke="{C['amber']}" stroke-width="5" stroke-linecap="round"/>
  <circle cx="{cx:.1f}" cy="{cy + 22:.1f}" r="3.5" fill="{C['amber']}"/>
"""


def svg_cover_loop() -> str:
    body = f"""
  <circle cx="315" cy="255" r="235" fill="url(#glowViolet)"/>
  <circle cx="315" cy="255" r="160" fill="url(#glowCyan)"/>

  <!-- Faded distant cloud -->
  {icon_cloud(600, 96, scale=0.84, stroke=C['dim'], fill=C['panel'], opacity=0.55, dashed=True)}
  <text x="600" y="164" text-anchor="middle" font-family="{SANS}" font-size="22" font-weight="600" fill="{C['dim']}" opacity="0.85">cluster CI</text>
  <path d="M 480 165 L 526 132" stroke="{C['border']}" stroke-width="4" stroke-dasharray="8 8" stroke-linecap="round"/>

  <!-- Outer loop arc (top/right) -->
  <path d="M 155 182 A 178 178 0 1 1 142 308" fill="none" stroke="{C['violet']}" stroke-width="6" stroke-linecap="round"/>
  <polygon points="155,158 132,192 174,194" fill="{C['violet']}"/>

  <!-- Inner accent arc (bottom) -->
  <path d="M 468 332 A 178 178 0 0 1 172 358" fill="none" stroke="{C['cyan']}" stroke-width="6" stroke-linecap="round" opacity="0.85"/>

  <!-- Central laptop -->
  {icon_laptop(315, 245, w=226, h=144, stroke=C['blue'], fill=C['panel'])}
  <text x="236" y="226" font-family="{MONO}" font-size="24" font-weight="700" fill="{C['violet']}">$&gt;</text>
  <rect x="276" y="210" width="122" height="14" rx="7" fill="{C['text']}" opacity="0.85"/>
  <rect x="236" y="246" width="138" height="12" rx="6" fill="{C['cyan']}" opacity="0.75"/>
  <rect x="236" y="274" width="94" height="12" rx="6" fill="{C['green']}" opacity="0.85"/>

  <!-- Plan document on left of loop -->
  {icon_document(86, 198, 92, 110, stroke=C['violet'], fill=C['panel'], fold=22)}
  <text x="132" y="238" text-anchor="middle" font-family="{MONO}" font-size="20" font-weight="700" fill="{C['violet']}">plan</text>
  <line x1="106" y1="258" x2="158" y2="258" stroke="{C['violet']}" stroke-width="4" stroke-linecap="round"/>
  <line x1="106" y1="276" x2="150" y2="276" stroke="{C['dim']}" stroke-width="4" stroke-linecap="round"/>
  <line x1="106" y1="294" x2="142" y2="294" stroke="{C['green']}" stroke-width="4" stroke-linecap="round"/>

  <!-- Check badge on right of loop -->
  {badge_check(482, 255, r=32)}

  <!-- Bottom pill label cleanly separated from loop -->
  <rect x="155" y="452" width="320" height="50" rx="14" fill="{C['panel']}" stroke="{C['violet']}" stroke-width="3"/>
  <text x="315" y="485" text-anchor="middle" font-family="{SANS}" font-size="23" font-weight="700" fill="{C['text']}">local edit loop · no model</text>
"""
    return wrap_svg("", body)


def svg_generic_vs_repo() -> str:
    body = f"""
  <circle cx="190" cy="270" r="185" fill="url(#glowGreen)"/>
  <circle cx="530" cy="270" r="185" fill="url(#glowRed)"/>

  <rect x="38" y="62" width="300" height="416" rx="24" fill="{C['panel']}" stroke="{C['border']}" stroke-width="3"/>
  <rect x="382" y="62" width="300" height="416" rx="24" fill="{C['panel']}" stroke="{C['red']}" stroke-width="4"/>

  <line x1="360" y1="92" x2="360" y2="448" stroke="{C['border']}" stroke-width="3" stroke-dasharray="10 10"/>

  {icon_laptop(188, 200, w=200, h=128, stroke=C['green'], fill=C['bg2'])}
  <text x="188" y="208" text-anchor="middle" font-family="{MONO}" font-size="26" font-weight="700" fill="{C['green']}">go test</text>
  {badge_check(188, 350, r=36)}
  <text x="188" y="432" text-anchor="middle" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['green']}">PASS</text>

  {icon_document(428, 108, 208, 174, stroke=C['red'], fill=C['bg2'], fold=32)}
  <text x="532" y="156" text-anchor="middle" font-family="{MONO}" font-size="22" font-weight="700" fill="{C['text']}">linter.yaml</text>
  <rect x="454" y="178" width="144" height="12" rx="6" fill="{C['red']}" opacity="0.85"/>
  <rect x="454" y="204" width="120" height="12" rx="6" fill="{C['amber']}" opacity="0.85"/>
  <rect x="454" y="230" width="152" height="12" rx="6" fill="{C['dim']}" opacity="0.65"/>
  <rect x="454" y="254" width="96" height="10" rx="5" fill="{C['dim']}" opacity="0.45"/>

  {badge_cross(532, 350, r=36)}
  <text x="532" y="432" text-anchor="middle" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['red']}">CI rules</text>
"""
    return wrap_svg("", body)


def svg_source_diff() -> str:
    body = f"""
  <circle cx="360" cy="280" r="240" fill="url(#glowViolet)"/>

  <rect x="48" y="56" width="624" height="428" rx="24" fill="{C['panel']}" stroke="{C['border']}" stroke-width="4"/>
  <path d="M 48 128 H 672" stroke="{C['border']}" stroke-width="3"/>

  <circle cx="92" cy="92" r="10" fill="{C['red']}"/>
  <circle cx="124" cy="92" r="10" fill="{C['amber']}"/>
  <circle cx="156" cy="92" r="10" fill="{C['green']}"/>
  <text x="360" y="100" text-anchor="middle" font-family="{MONO}" font-size="24" font-weight="700" fill="{C['text']}">validate --diff</text>

  <rect x="82" y="160" width="556" height="96" rx="16" fill="{C['red']}" fill-opacity="0.14" stroke="{C['red']}" stroke-width="4"/>
  <text x="112" y="217" font-family="{MONO}" font-size="32" font-weight="700" fill="{C['red']}">-</text>
  <text x="152" y="216" font-family="{MONO}" font-size="24" font-weight="700" fill="{C['red']}">base_sha..HEAD</text>
  <rect x="406" y="200" width="196" height="14" rx="7" fill="{C['red']}" opacity="0.45"/>

  <path d="M 360 268 V 298" stroke="{C['violet']}" stroke-width="5" stroke-linecap="round"/>
  <polygon points="360,312 345,290 375,290" fill="{C['violet']}"/>

  <rect x="82" y="320" width="556" height="126" rx="16" fill="{C['green']}" fill-opacity="0.14" stroke="{C['green']}" stroke-width="4"/>
  <text x="112" y="372" font-family="{MONO}" font-size="32" font-weight="700" fill="{C['green']}">+</text>
  <text x="152" y="371" font-family="{MONO}" font-size="24" font-weight="700" fill="{C['green']}">$BASE_SHA</text>
  <rect x="328" y="356" width="274" height="14" rx="7" fill="{C['green']}" opacity="0.55"/>
  <text x="152" y="416" font-family="{MONO}" font-size="23" font-weight="700" fill="{C['cyan']}">git ls-files</text>
  <rect x="352" y="401" width="216" height="14" rx="7" fill="{C['cyan']}" opacity="0.55"/>
"""
    return wrap_svg("", body)


def svg_unresolved_gate() -> str:
    body = f"""
  <circle cx="360" cy="360" r="220" fill="url(#glowAmber)"/>

  <!-- Row 1: Runnable -->
  <rect x="52" y="58" width="616" height="122" rx="22" fill="{C['panel']}" stroke="{C['green']}" stroke-width="4"/>
  {badge_check(118, 119, r=34)}
  <text x="180" y="114" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['text']}">run</text>
  <rect x="180" y="134" width="236" height="14" rx="7" fill="{C['green']}" opacity="0.65"/>
  <rect x="502" y="90" width="132" height="58" rx="14" fill="{C['bg1']}" stroke="{C['cyan']}" stroke-width="3"/>
  <text x="568" y="127" text-anchor="middle" font-family="{MONO}" font-size="23" font-weight="700" fill="{C['cyan']}">image</text>

  <!-- Row 2: Skipped -->
  <rect x="52" y="208" width="616" height="122" rx="22" fill="{C['panel']}" stroke="{C['border']}" stroke-width="4"/>
  <circle cx="118" cy="269" r="34" fill="{C['bg2']}" stroke="{C['dim']}" stroke-width="5"/>
  <path d="M 102 269 H 134 M 124 257 L 136 269 L 124 281" fill="none" stroke="{C['dim']}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="180" y="264" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['dim']}">skip</text>
  <rect x="180" y="284" width="186" height="14" rx="7" fill="{C['dim']}" opacity="0.45"/>

  <!-- Row 3: Unresolved blocks scope -->
  <rect x="52" y="358" width="616" height="124" rx="22" fill="{C['panel']}" stroke="{C['amber']}" stroke-width="5"/>
  {badge_question(118, 420, r=34)}
  <text x="180" y="414" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['amber']}">unresolved</text>
  <rect x="180" y="434" width="216" height="14" rx="7" fill="{C['amber']}" opacity="0.65"/>

  <!-- Stop gate icon on right of row 3 -->
  <rect x="492" y="388" width="142" height="64" rx="14" fill="{C['amber']}" fill-opacity="0.16" stroke="{C['amber']}" stroke-width="4"/>
  <circle cx="528" cy="420" r="14" fill="none" stroke="{C['amber']}" stroke-width="4"/>
  <line x1="518" y1="430" x2="538" y2="410" stroke="{C['amber']}" stroke-width="4"/>
  <text x="586" y="428" text-anchor="middle" font-family="{SANS}" font-size="23" font-weight="700" fill="{C['amber']}">block</text>
"""
    return wrap_svg("", body)


def svg_run_container() -> str:
    body = f"""
  <circle cx="485" cy="270" r="220" fill="url(#glowCyan)"/>
  <circle cx="165" cy="270" r="150" fill="url(#glowViolet)"/>

  {icon_document(54, 134, 200, 272, stroke=C['violet'], fill=C['panel'], fold=38)}
  <text x="154" y="210" text-anchor="middle" font-family="{MONO}" font-size="25" font-weight="700" fill="{C['violet']}">plan.json</text>
  <line x1="90" y1="250" x2="218" y2="250" stroke="{C['violet']}" stroke-width="5" stroke-linecap="round"/>
  <line x1="90" y1="284" x2="194" y2="284" stroke="{C['dim']}" stroke-width="5" stroke-linecap="round"/>
  <line x1="90" y1="318" x2="208" y2="318" stroke="{C['dim']}" stroke-width="5" stroke-linecap="round"/>
  <line x1="90" y1="352" x2="168" y2="352" stroke="{C['green']}" stroke-width="5" stroke-linecap="round"/>

  <path d="M 272 270 H 344" stroke="{C['cyan']}" stroke-width="6" stroke-linecap="round"/>
  <polygon points="362,270 336,252 336,288" fill="{C['cyan']}"/>

  {icon_container(374, 114, 292, 312, stroke=C['cyan'], fill=C['panel'])}
  <rect x="410" y="164" width="220" height="148" rx="16" fill="{C['bg2']}" stroke="{C['border']}" stroke-width="3"/>
  <text x="436" y="216" font-family="{MONO}" font-size="25" font-weight="700" fill="{C['cyan']}">/workspace</text>
  <rect x="436" y="242" width="156" height="14" rx="7" fill="{C['text']}" opacity="0.75"/>
  <rect x="436" y="272" width="118" height="14" rx="7" fill="{C['green']}" opacity="0.85"/>

  <text x="495" y="380" text-anchor="middle" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['text']}">Docker</text>
  {badge_check(622, 382, r=38)}
"""
    return wrap_svg("", body)


def svg_two_checks() -> str:
    body = f"""
  <circle cx="205" cy="235" r="175" fill="url(#glowRed)"/>
  <circle cx="515" cy="235" r="175" fill="url(#glowRed)"/>

  <!-- Card 1: codespell -->
  <rect x="44" y="60" width="302" height="292" rx="22" fill="{C['panel']}" stroke="{C['red']}" stroke-width="4"/>
  <text x="195" y="116" text-anchor="middle" font-family="{MONO}" font-size="25" font-weight="700" fill="{C['text']}">codespell</text>
  <rect x="74" y="146" width="242" height="88" rx="14" fill="{C['bg2']}" stroke="{C['border']}" stroke-width="3"/>
  <text x="195" y="198" text-anchor="middle" font-family="{MONO}" font-size="28" font-weight="700" fill="{C['red']}">"teh"</text>
  <path d="M 140 212 Q 158 224 176 212 T 212 212 T 248 212" fill="none" stroke="{C['red']}" stroke-width="4" stroke-linecap="round"/>
  {badge_cross(195, 292, r=32)}

  <!-- Card 2: test name convention -->
  <rect x="374" y="60" width="302" height="292" rx="22" fill="{C['panel']}" stroke="{C['red']}" stroke-width="4"/>
  <text x="525" y="116" text-anchor="middle" font-family="{MONO}" font-size="25" font-weight="700" fill="{C['text']}">test name</text>
  <rect x="404" y="146" width="242" height="88" rx="14" fill="{C['bg2']}" stroke="{C['border']}" stroke-width="3"/>
  <text x="525" y="198" text-anchor="middle" font-family="{MONO}" font-size="25" font-weight="700" fill="{C['red']}">TestBad_Name</text>
  <line x1="434" y1="214" x2="616" y2="214" stroke="{C['red']}" stroke-width="4" stroke-linecap="round"/>
  {badge_cross(525, 292, r=32)}

  <!-- Bottom warning bar: t.Errorf -->
  <rect x="44" y="382" width="632" height="98" rx="22" fill="{C['panel']}" stroke="{C['amber']}" stroke-width="4"/>
  <polygon points="114,406 144,456 84,456" fill="none" stroke="{C['amber']}" stroke-width="5" stroke-linejoin="round"/>
  <line x1="114" y1="422" x2="114" y2="437" stroke="{C['amber']}" stroke-width="5" stroke-linecap="round"/>
  <circle cx="114" cy="447" r="3.5" fill="{C['amber']}"/>
  <text x="176" y="440" font-family="{MONO}" font-size="26" font-weight="700" fill="{C['amber']}">t.Errorf</text>
  <rect x="330" y="424" width="184" height="14" rx="7" fill="{C['amber']}" opacity="0.55"/>
  <text x="590" y="440" text-anchor="middle" font-family="{SANS}" font-size="25" font-weight="700" fill="{C['amber']}">warn</text>
"""
    return wrap_svg("", body)


def svg_scope_grid() -> str:
    cells = []
    cols = 5
    rows = 3
    w = 104
    h = 90
    gap_x = 22
    gap_y = 22
    start_x = (720 - (cols * w + (cols - 1) * gap_x)) / 2
    start_y = 68

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            x = start_x + c * (w + gap_x)
            y = start_y + r * (h + gap_y)
            if idx < 2:
                cells.append(f"""
  <rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="18" fill="{C['violet']}" fill-opacity="0.24" stroke="{C['violet']}" stroke-width="5"/>
  <circle cx="{x + w/2:.1f}" cy="{y + h/2:.1f}" r="20" fill="{C['panel']}" stroke="{C['green']}" stroke-width="4"/>
  <path d="M {x + w/2 - 9:.1f} {y + h/2 + 1:.1f} L {x + w/2 - 2:.1f} {y + h/2 + 8:.1f} L {x + w/2 + 10:.1f} {y + h/2 - 6:.1f}" fill="none" stroke="{C['green']}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>""")
            else:
                cells.append(f"""
  <rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="18" fill="{C['panel']}" stroke="{C['border']}" stroke-width="3" opacity="0.7"/>
  <line x1="{x + 28:.1f}" y1="{y + h/2:.1f}" x2="{x + w - 28:.1f}" y2="{y + h/2:.1f}" stroke="{C['dim']}" stroke-width="4" stroke-linecap="round" opacity="0.45"/>""")

    cells_str = "\n".join(cells)
    body = f"""
  <circle cx="195" cy="125" r="155" fill="url(#glowViolet)"/>
  {cells_str}
  <rect x="160" y="408" width="400" height="72" rx="20" fill="{C['panel']}" stroke="{C['violet']}" stroke-width="4"/>
  <text x="360" y="454" text-anchor="middle" font-family="{SANS}" font-size="28" font-weight="700" fill="{C['text']}">2 of 15 selected</text>
"""
    return wrap_svg("", body)


def svg_cluster_ci_rest() -> str:
    body = f"""
  <circle cx="190" cy="270" r="180" fill="url(#glowBlue)"/>
  <circle cx="525" cy="270" r="190" fill="url(#glowViolet)"/>

  <!-- Left: Local checks -->
  <rect x="38" y="64" width="286" height="412" rx="24" fill="{C['panel']}" stroke="{C['blue']}" stroke-width="4"/>
  {icon_laptop(181, 200, w=194, h=124, stroke=C['blue'], fill=C['bg2'])}
  {badge_check(181, 196, r=30)}
  <rect x="98" y="322" width="166" height="14" rx="7" fill="{C['blue']}" opacity="0.65"/>
  <rect x="118" y="352" width="126" height="14" rx="7" fill="{C['dim']}" opacity="0.45"/>
  <text x="181" y="432" text-anchor="middle" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['text']}">local checks</text>

  <!-- Right: Cluster CI -->
  <rect x="360" y="64" width="322" height="412" rx="24" fill="{C['panel']}" stroke="{C['violet']}" stroke-width="4"/>
  {icon_cloud(521, 184, scale=1.12, stroke=C['violet'], fill=C['bg2'], opacity=1.0)}

  <!-- Key / Secret icon inside cloud area -->
  <circle cx="476" cy="182" r="14" fill="none" stroke="{C['amber']}" stroke-width="5"/>
  <path d="M 490 182 H 524 V 194 M 510 182 V 192" fill="none" stroke="{C['amber']}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>

  <!-- Release shield icon inside cloud area -->
  <path d="M 558 156 L 586 166 V 188 C 586 204 572 214 558 220 C 544 214 530 204 530 188 V 166 Z" fill="none" stroke="{C['cyan']}" stroke-width="4" stroke-linejoin="round"/>

  <!-- Labeled rows under cloud -->
  <rect x="400" y="276" width="242" height="48" rx="12" fill="{C['bg2']}" stroke="{C['amber']}" stroke-width="3"/>
  <circle cx="430" cy="300" r="8" fill="{C['amber']}"/>
  <text x="454" y="308" font-family="{SANS}" font-size="22" font-weight="600" fill="{C['amber']}">secrets</text>

  <rect x="400" y="340" width="242" height="48" rx="12" fill="{C['bg2']}" stroke="{C['cyan']}" stroke-width="3"/>
  <circle cx="430" cy="364" r="8" fill="{C['cyan']}"/>
  <text x="454" y="372" font-family="{SANS}" font-size="22" font-weight="600" fill="{C['cyan']}">releases</text>

  <text x="521" y="432" text-anchor="middle" font-family="{SANS}" font-size="26" font-weight="700" fill="{C['text']}">cluster CI</text>
"""
    return wrap_svg("", body)


def svg_convert_once() -> str:
    body = f"""
  <circle cx="360" cy="270" r="220" fill="url(#glowViolet)"/>

  <!-- Left: Repo YAML + helpers -->
  {icon_document(52, 112, 152, 136, stroke=C['blue'], fill=C['panel'], fold=26)}
  <text x="128" y="190" text-anchor="middle" font-family="{MONO}" font-size="24" font-weight="700" fill="{C['blue']}">YAML</text>

  {icon_document(52, 286, 152, 136, stroke=C['cyan'], fill=C['panel'], fold=26)}
  <text x="128" y="364" text-anchor="middle" font-family="{MONO}" font-size="24" font-weight="700" fill="{C['cyan']}">scripts</text>

  <!-- Arrows into Agent -->
  <path d="M 216 180 L 284 234" stroke="{C['blue']}" stroke-width="5" stroke-linecap="round"/>
  <path d="M 216 354 L 284 300" stroke="{C['cyan']}" stroke-width="5" stroke-linecap="round"/>

  <!-- Center: Agent -->
  {icon_agent(360, 268, size=118, stroke=C['violet'], fill=C['panel'])}

  <!-- Arrow to Reviewed Plan -->
  <path d="M 436 268 H 498" stroke="{C['violet']}" stroke-width="6" stroke-linecap="round"/>
  <polygon points="514,268 490,252 490,284" fill="{C['violet']}"/>

  <!-- Right: Reviewed Plan -->
  {icon_document(524, 146, 148, 244, stroke=C['green'], fill=C['panel'], fold=30)}
  <line x1="552" y1="216" x2="642" y2="216" stroke="{C['text']}" stroke-width="5" stroke-linecap="round"/>
  <line x1="552" y1="248" x2="626" y2="248" stroke="{C['dim']}" stroke-width="5" stroke-linecap="round"/>
  <line x1="552" y1="280" x2="634" y2="280" stroke="{C['dim']}" stroke-width="5" stroke-linecap="round"/>
  {badge_check(598, 338, r=30)}
"""
    return wrap_svg("", body)


def main() -> None:
    files = {
        "cover-loop.svg": svg_cover_loop(),
        "generic-vs-repo.svg": svg_generic_vs_repo(),
        "convert-once.svg": svg_convert_once(),
        "source-diff.svg": svg_source_diff(),
        "unresolved-gate.svg": svg_unresolved_gate(),
        "run-container.svg": svg_run_container(),
        "two-checks.svg": svg_two_checks(),
        "scope-grid.svg": svg_scope_grid(),
        "cluster-ci-rest.svg": svg_cluster_ci_rest(),
    }
    for name, content in files.items():
        out = ART_DIR / name
        ET.fromstring(content)
        out.write_text(content.strip() + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(ART_DIR.parent)} ({len(content)} bytes)")


if __name__ == "__main__":
    main()
