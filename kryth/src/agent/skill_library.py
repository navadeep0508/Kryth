"""Skill library — built-in system-prompt fragments for major stacks
and project archetypes.

Each skill is a focused instruction set that is injected as an extra
system message for the turn. Skills compose: a request for "a SaaS
dashboard built with React + FastAPI" can auto-inject ``system`` +
``saas`` + ``react`` + ``fastapi`` together.

Add a new stack by appending an entry to ``SKILLS`` and (optionally)
adding a keyword match in ``agent/skills.py:AUTO_SKILL_RULES``.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Master orchestration skill
# ---------------------------------------------------------------------------

SYSTEM = """[Master skill: build a complete, production-quality system]

Treat this request as a real engineering project. Match what a senior
engineer would ship after a focused work session — not a tutorial stub.

PROCESS:

1. PLAN. Call todo_write FIRST with a file-level breakdown. List every
   file you will create with a one-line purpose. Aim for 6-20 files for
   a real app, not 1-3.

2. SCAFFOLD. Create directories with run_command before writing files.
   Use forward slashes; `mkdir -p` works on Git Bash for Windows too.

3. WRITE EACH FILE COMPLETELY. Every file gets real, working code on
   the first pass. No "// TODO", no "pass # implement later", no
   placeholder data. Config files (package.json, requirements.txt,
   pyproject.toml, tsconfig.json) populated with the real deps and
   scripts for the chosen stack.

4. STYLING & POLISH. For any UI: real design system (palette,
   typography, spacing scale). At least 3 distinct sections per page.
   Responsive. Hover/focus states. Real copy — write actual headings
   and body text, not lorem ipsum.

5. WIRE BEHAVIOR. Buttons do something. Forms validate. APIs return
   real shapes. No dead UI. No `console.log("clicked")` stubs.

6. INTEGRATE. If the system has frontend + backend, wire them
   end-to-end: real ports, real fetch calls, real CORS rules.

7. SMOKE CHECK. At minimum one test file or a manual verification
   script (a `make_request.py`, a `curl` line in the README, a
   playwright smoke test).

8. README + ENV. README with run instructions, env vars, architecture
   overview. `.env.example` if any secrets are needed.

9. VERIFY. Run the project with run_command. Open the page, hit the
   endpoint, run the script. If it fails, iterate until it works.

10. SUMMARIZE in one sentence what was built.

FILE COUNT EXPECTATIONS:
- Static landing page: 3-5 files (index.html, styles.css, script.js,
  optional assets/, README).
- Small web app: 10-20 files.
- Full-stack project: 15-30 files split between frontend/ and backend/.
- CLI tool: 5-10 files (entrypoint, modules, tests, README,
  requirements.txt or package.json).
"""


# ---------------------------------------------------------------------------
# Project archetypes (compose with stack skills)
# ---------------------------------------------------------------------------

LANDING_PAGE = """[Archetype: landing page — UI-heavy, marketing-grade]

You are building a real marketing landing page that a founder would
proudly link from their Twitter bio. Stub HTML with three cards and
default Arial is REJECTED.

HARD LENGTH FLOORS — if your output is below these, you are not done:
- index.html : >= 6000 characters
- styles.css : >= 4000 characters
- script.js  : >= 800 characters (real behavior, not console.log)

REQUIRED SECTIONS (in this order; skipping one = incomplete):

1. Sticky top nav. Logo (text or inline SVG mark) on the left, 4-6 nav
   links centered, primary CTA button on the right. Mobile: hamburger
   that opens a slide-in panel.

2. HERO. Eyebrow tag/badge (small caps, accent color). Big headline
   (h1, ~48-72px desktop, multi-line with one word in accent color or
   gradient text). Subheadline paragraph (~2 lines, real value-prop
   copy). Two buttons side by side: primary (filled) + secondary
   (ghost). Below the buttons, either a trust strip ("Trusted by 5,000+
   teams" + logos) or a hero visual (inline SVG illustration / mock
   product card / stat counters). Background: subtle gradient or
   radial-gradient orb, not flat color.

3. FEATURES GRID. Section eyebrow + h2 + subhead. 6 cards minimum,
   each with: inline SVG icon (24-32px, accent-colored), feature title
   (h3), 2-3 line description with real concrete copy. Cards use the
   shared card style (radius, shadow, hover lift).

4. HOW IT WORKS. 3 numbered steps. Each step: big number/icon on the
   left, title + description on the right. Connector line (dashed or
   gradient) between them.

5. SOCIAL PROOF. Either testimonials (3 cards: quote, author name,
   role/company, avatar circle) or a logo wall ("As featured in"
   followed by 6+ company name pills).

6. PRICING. 3 tiers (Starter / Pro / Enterprise or similar). Each
   card: tier name, price, billing period, feature checklist (4-6
   items), CTA button. The middle tier is highlighted ("Most popular"
   ribbon, bigger shadow, accent border).

7. FAQ. 5+ questions. Use a <details>/<summary> accordion OR a JS
   accordion with smooth height transition. Real Q&A copy.

8. FINAL CTA SECTION. Full-bleed strip with strong headline + button,
   visually distinct (dark or gradient background).

9. FOOTER. 4 columns of links (Product, Company, Resources, Legal),
   small social-icon row, copyright line. NOT a single centered
   "© 2024" line.

REQUIRED HTML CONVENTIONS:
- DOCTYPE html, lang attribute, viewport meta, theme-color meta.
- Semantic landmarks: <header><nav>, <main>, <section> per major
  block with descriptive id, <footer>.
- Heading order: one <h1> in hero, <h2> per section, <h3> within.
- Inline SVG icons throughout (use simple paths — feather/lucide
  style: stroke=currentColor, fill=none, stroke-width=2,
  stroke-linecap=round, stroke-linejoin=round).
- ARIA: aria-label on icon-only buttons, aria-expanded on the mobile
  menu trigger and FAQ accordions.

DO NOT:
- Use Arial / Helvetica as the headline font. Pull Inter, Space
  Grotesk, Manrope, Geist, or Sora from Google Fonts via @import in
  styles.css.
- Use bootstrap-blue (#007bff) or boring grey (#333) as the accent.
  Pick a real palette (see modern-ui skill).
- Ship a 200-char script.js with only console.log.
- Repeat <h1> in non-hero sections.
- Use <div class="logo"><h1>...</h1></div> — the logo is decoration,
  not a top heading. Use a <a class="logo"> or <span> instead.
"""


MODERN_UI = """[Skill: modern UI polish — design-system layer]

Compose this with any UI archetype (landing-page, dashboard, saas) for
output that actually looks designed.

TYPOGRAPHY:
- @import a real display font at the top of styles.css. Pick one of:
  Inter, Space Grotesk, Manrope, Geist, Sora, Plus Jakarta Sans, DM
  Sans. Use weights 400/500/600/700. Example:
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
- Body: 16-17px, line-height 1.6, font-weight 400.
- Headings: tight line-height (1.1-1.2), weight 600-700, letter-spacing
  -0.02em on the biggest sizes.
- A type scale: define --font-xs 12, --font-sm 14, --font-base 16,
  --font-lg 18, --font-xl 24, --font-2xl 32, --font-3xl 48, --font-4xl 64.

COLOR PALETTE — pick ONE and commit:
- "Indigo dusk": bg #0a0a0f, fg #f8fafc, muted #94a3b8, accent #818cf8,
  accent-2 #c084fc, surface #14141e, border #1f1f2e.
- "Mint terminal": bg #0a0f0d, fg #e6fffa, muted #6ee7b7, accent #10b981,
  accent-2 #06b6d4, surface #102923, border #134e4a.
- "Sunset peach": bg #fef6f0, fg #1a0f0a, muted #78716c, accent #f97316,
  accent-2 #ef4444, surface #ffffff, border #fed7aa.
- "Pure light": bg #ffffff, fg #0a0a0a, muted #6b7280, accent #6366f1,
  accent-2 #ec4899, surface #f9fafb, border #e5e7eb.
Define ALL of these as CSS variables on :root. NEVER hardcode a hex
value below :root.

SPACING SCALE (rem-based):
  --space-1: 0.25rem;  --space-2: 0.5rem;   --space-3: 0.75rem;
  --space-4: 1rem;     --space-6: 1.5rem;   --space-8: 2rem;
  --space-12: 3rem;    --space-16: 4rem;    --space-24: 6rem;
  --space-32: 8rem;

RADIUS SCALE:
  --radius-sm: 6px; --radius: 12px; --radius-lg: 16px;
  --radius-xl: 24px; --radius-full: 9999px;

SHADOW RAMP:
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
  --shadow-glow: 0 0 0 1px var(--accent), 0 12px 24px -8px color-mix(in oklch, var(--accent) 60%, transparent);

TECHNIQUES YOU MUST USE:
- Gradients on hero text or button: background:
  linear-gradient(135deg, var(--accent), var(--accent-2));
  -webkit-background-clip:text; color:transparent;
- Soft layered shadows on cards (use --shadow-md, --shadow-lg).
- Glassmorphism on the sticky nav: backdrop-filter: blur(12px) +
  semi-transparent background.
- Hover micro-interaction on cards/buttons: transition transform +
  shadow + border-color over ~200ms. translateY(-2px) on hover.
- Smooth scroll: html { scroll-behavior: smooth; }
- Responsive: every grid uses grid-template-columns:
  repeat(auto-fit, minmax(280px, 1fr)) or media-query breakpoints at
  640/768/1024/1280.
- Focus states: outline: 2px solid var(--accent); outline-offset: 2px;

REQUIRED JAVASCRIPT BEHAVIORS:
- Mobile menu drawer: click toggles aria-expanded and a body class
  that triggers a translateX transform on the drawer.
- Scroll-reveal: IntersectionObserver that adds a 'visible' class to
  sections; CSS animates opacity/translateY from-to.
- FAQ accordion: click toggles aria-expanded and a max-height
  transition on the answer.
- Sticky-nav state: window.scroll listener that adds a
  'nav-scrolled' class once past 40px (smaller padding + stronger
  shadow).
- Active-link highlight: IntersectionObserver on sections updates
  the matching nav link's aria-current.
- Smooth-scroll polyfill for nav anchor clicks (preventDefault +
  scrollIntoView with behavior smooth).
"""


DASHBOARD = """[Archetype: dashboard / admin UI]

A dashboard is sidebar + topbar + main grid of cards/charts/tables.

- Sidebar: 4-8 nav items with icons.
- Topbar: search, notifications, user menu.
- Main: stat cards row, primary chart panel, recent-activity table.
- Real (seeded) data — don't show "0 / 0 / 0" everywhere. Generate
  plausible numbers or load a sample dataset.
- Responsive: sidebar collapses on mobile.
- At least one interactive widget (filter, date range, sortable table).
"""


SAAS = """[Archetype: SaaS product]

Multi-page: marketing landing + app shell + auth screens.

- Pages: `/` (landing), `/login`, `/signup`, `/app/dashboard`,
  `/app/settings`, optional `/pricing`.
- Auth: signup → email + password (validation real on the client),
  protected route guard for `/app/*`. Persisted session via
  localStorage or cookie; if backend is included, use proper sessions/
  JWT.
- App shell: sidebar nav, top bar, content area.
- Real content on every page.
"""


API_BACKEND = """[Archetype: REST API backend]

- Health endpoint at GET /healthz.
- CRUD for the primary resource (list, get, create, update, delete).
- Validation on every input (Pydantic for FastAPI, zod for Express,
  serializers for Django/DRF).
- Errors returned as { "error": "...", "detail": ... } with proper
  HTTP status codes (400/401/403/404/409/422/500).
- CORS configured if a frontend will call it (default allow origin
  http://localhost:5173 or :3000).
- An `.env.example` plus a settings layer (pydantic-settings / dotenv /
  Django settings).
- A README curl block showing one example per endpoint.
"""


# ---------------------------------------------------------------------------
# Stack-specific skills
# ---------------------------------------------------------------------------

REACT = """[Stack: React]

- Use Vite (`npm create vite@latest`-style layout). React 18+, function
  components only, hooks. TypeScript by default unless the user opts
  out — `tsx` files, strict mode in tsconfig.
- File layout:
    src/
      main.tsx          # Vite entry, mounts <App/>
      App.tsx
      pages/            # one file per route if using react-router
      components/       # reusable presentational components
      hooks/            # custom hooks (use-prefixed)
      lib/              # client utils, API wrappers
      styles/           # global.css, vars.css; or use Tailwind
    index.html
    package.json
    tsconfig.json
    vite.config.ts
- State: `useState`/`useReducer` for local, `useContext` for app-level.
  Reach for Zustand or Redux Toolkit only when needed.
- Data fetching: `useEffect` + `fetch` for simple cases; suggest TanStack
  Query if the app has many endpoints.
- Routing: react-router-dom v6 (`createBrowserRouter`).
- Forms: controlled components; show real validation errors.
- Run with `npm install && npm run dev`; verify with run_command (give
  it a long timeout or use background).
"""


NEXTJS = """[Stack: Next.js]

- Next 14+ App Router. Default to server components; mark client
  components with `"use client"` only when state/effects are needed.
- File layout:
    app/
      layout.tsx        # root layout, html/body
      page.tsx          # `/`
      <segment>/page.tsx
      api/<route>/route.ts
      globals.css
    components/
    lib/
    public/
    next.config.mjs
    tailwind.config.ts (if Tailwind)
    package.json
    tsconfig.json
- Data: server-side `fetch` in server components; cache by default.
  `revalidate` or `cache: "no-store"` as needed.
- Forms: server actions for mutations.
- Styling: prefer Tailwind. Use CSS modules if Tailwind is declined.
- Run with `npm run dev` (port 3000).
"""


VUE = """[Stack: Vue]

- Vue 3 + Vite + `<script setup>` Composition API. TypeScript by default.
- File layout: src/main.ts, src/App.vue, src/components/*.vue,
  src/pages/*.vue (with vue-router), src/composables/*.ts.
- Pinia for state if more than local component state is needed.
- Single-file components: `<script setup>`, `<template>`, `<style scoped>`.
"""


SVELTE = """[Stack: Svelte / SvelteKit]

- SvelteKit (`npm create svelte@latest`). TypeScript by default.
- Routing via `src/routes/`. `+page.svelte`, `+page.ts` for data loading,
  `+layout.svelte` for shared chrome, `+server.ts` for endpoints.
- Stores in `src/lib/stores/`. Components in `src/lib/components/`.
"""


VITE = """[Tooling: Vite]

When the user wants "a Vite project" without naming a framework, ask
yourself which framework fits the prompt and pick one (React if web app
with components, vanilla TS if it's a small game/utility). Generate the
expected `vite.config.ts`, `index.html`, `src/main.ts(x)`, with the
right plugins (e.g. `@vitejs/plugin-react`).
"""


TAILWIND = """[Tooling: Tailwind CSS]

- Tailwind 3.x. Generate `tailwind.config.js`/`.ts`, `postcss.config.js`,
  and include `@tailwind base; @tailwind components; @tailwind utilities;`
  in the main CSS file.
- Use utility classes directly; extract to `@apply`-based components
  only for repeated patterns. Use the default palette but extend
  `theme.extend.colors` with a brand color when relevant.
- Configure `content: [...]` paths so unused classes are purged.
- Dark mode: enable `darkMode: "class"` and provide a toggle.
"""


FASTAPI = """[Stack: FastAPI]

- Python 3.10+, FastAPI, Uvicorn, Pydantic v2.
- File layout:
    app/
      main.py             # FastAPI() instance, includes routers
      config.py           # pydantic-settings, loads .env
      database.py         # SQLAlchemy engine / async session (if DB)
      models.py           # ORM models
      schemas.py          # Pydantic request/response models
      routers/<name>.py   # APIRouter per domain
      dependencies.py     # Depends() helpers (current_user, db)
    tests/test_<name>.py
    requirements.txt      # fastapi, uvicorn[standard], pydantic-settings, etc
    .env.example
    README.md
- Always include: CORS middleware, GET /healthz, request validation,
  HTTPException for errors, OpenAPI tags.
- Run with `uvicorn app.main:app --reload --port 8000`.
"""


FLASK = """[Stack: Flask]

- Application factory pattern (`create_app()`), blueprints per domain,
  Flask-SQLAlchemy if DB needed. python-dotenv for .env loading.
- File layout:
    app/
      __init__.py         # create_app
      config.py
      models.py
      blueprints/<name>.py
      templates/
      static/
    run.py                # `flask run` entry
    requirements.txt
    .env.example
- Routes return JSON via `jsonify(...)` for APIs; render_template for
  HTML pages.
"""


DJANGO = """[Stack: Django]

- Django 4.x or 5.x. Real project structure via `django-admin
  startproject` + `python manage.py startapp <name>`.
- File layout:
    project/
      settings.py         # split into base/dev/prod if production
      urls.py
      wsgi.py / asgi.py
    apps/<name>/
      models.py
      views.py (or views/ package)
      urls.py
      admin.py
      migrations/
      templates/<name>/
    static/
    requirements.txt
- For an API: add Django REST Framework, serializers, viewsets, routers.
- Always include migrations (`python manage.py makemigrations`).
"""


EXPRESS = """[Stack: Express (Node.js)]

- TypeScript by default. Node 18+.
- File layout:
    src/
      index.ts            # creates app, listens
      app.ts              # express() instance, middleware, routes
      routes/<name>.ts    # express.Router()
      controllers/<name>.ts
      middleware/
      lib/                # db client, helpers
      types/
    package.json          # `dev`: tsx watch, `start`: node dist
    tsconfig.json
    .env.example
    README.md
- Middleware: helmet, cors, express.json, morgan in dev.
- Errors: central error handler middleware, async route wrapper.
- Validation: zod schemas.
"""


NESTJS = """[Stack: NestJS]

- Nest CLI layout. Modules per domain, controllers, services, DTOs.
- File layout (per module):
    src/
      main.ts
      app.module.ts
      <feature>/
        <feature>.module.ts
        <feature>.controller.ts
        <feature>.service.ts
        dto/
- TypeORM or Prisma for DB. class-validator for DTO validation.
"""


PYTHON_CLI = """[Stack: Python CLI]

- Use `typer` (modern, type-hint-driven) by default; fall back to
  `argparse` only if the user objects to a dependency.
- File layout:
    src/<pkgname>/
      __init__.py
      __main__.py         # so `python -m pkgname` works
      cli.py              # typer.Typer() app, commands
      core.py             # the actual logic
    tests/test_cli.py
    pyproject.toml        # entry_points / [project.scripts]
    requirements.txt
    README.md
- The pyproject `[project.scripts]` entry must wire `pkgname = "pkgname.cli:app"`.
- Use `rich` for any user-facing output (tables, progress, colour).
- Real error messages with non-zero exits on failure.
"""


NODE_CLI = """[Stack: Node CLI]

- TypeScript by default. `commander` for command parsing,
  `chalk`/`picocolors` for colour, `ora` for spinners.
- File layout:
    src/
      cli.ts              # commander program + parse
      commands/<name>.ts
      lib/
    package.json          # `bin`: { "<name>": "dist/cli.js" }
    tsconfig.json
    README.md
- Build step: tsc to dist/, add a `#!/usr/bin/env node` shebang on the
  built cli.js. Add a `prepublishOnly` script.
"""


ELECTRON = """[Stack: Electron]

- Main process in `main/index.ts`, preload in `preload/index.ts`,
  renderer (React or vanilla) in `renderer/`.
- contextIsolation: true, nodeIntegration: false. Expose APIs via
  contextBridge in preload.
- Vite for the renderer build (electron-vite or vite-plugin-electron).
- package.json `main: "main/index.js"` after build.
"""


SKILLS: dict[str, str] = {
    "system": SYSTEM,
    "modern-ui": MODERN_UI,
    "landing-page": LANDING_PAGE,
    "dashboard": DASHBOARD,
    "saas": SAAS,
    "api": API_BACKEND,
    "react": REACT,
    "nextjs": NEXTJS,
    "vue": VUE,
    "svelte": SVELTE,
    "vite": VITE,
    "tailwind": TAILWIND,
    "fastapi": FASTAPI,
    "flask": FLASK,
    "django": DJANGO,
    "express": EXPRESS,
    "nestjs": NESTJS,
    "python-cli": PYTHON_CLI,
    "node-cli": NODE_CLI,
    "electron": ELECTRON,
}
