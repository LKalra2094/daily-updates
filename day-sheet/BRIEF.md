# Day Sheet — a surface for executing one day

Start here. This is the spec and the accumulated context; there is no code yet.

A working prototype of the interface exists:
**https://claude.ai/code/artifact/3b9e3280-be5b-4e41-8abb-07962cc2031f**
It is static HTML with `localStorage` ticks. Look at it before reading further -
the layout is the argument.

## The idea

Google Calendar and Todoist stop being places you work from and become **capture
points only**. When something comes to mind, it goes in one of them and is
forgotten about.

Every morning a model reads both, plus what it knows about the shape of the
reader's life, and writes a plan for that one day into a third surface. The day
is then executed from that surface alone, ticking things off as they are done.

The distinction that matters: capture is for everything, forever. The day sheet
is for today, and it is thrown away tomorrow. It is not a life manager and must
never grow into one.

## Why the third surface

A task list answers *what*, never *when*. The planning step - deciding what
actually fits in the hours that exist - is the thing no tool in the stack does,
and it is the thing being built.

## What the model is given

Already computed and working in `daily-updates/`:

- **Events** for today, from the Google Calendar iCal feed (`gcal.py`)
- **Tasks** due or overdue, from Todoist v1, with notes, deadlines, estimates,
  labels, days late, and how many times each has been postponed (`briefing.py`)
- **Free windows**, computed in Python from a fixed weekly shape minus the
  calendar (`plan.py`, `week.json`)
- **F1 sessions** for today - qualifying, race, sprint, sprint qualifying -
  from Jolpica (`f1.py`)
- **Habits still owed this week**, from the habits bot's database (`plan.py`)

Still to add, and the part that makes this personal rather than generic:

- **Lifestyle rules.** Eat about four hours before sleeping. Finish the gym
  about three hours before sleeping. Others to be collected. Some are general
  health practice, some are specific to this reader.
- **Week shape** already exists in `week.json` (office Mon-Thu 7-5, Friday
  home by 3, weekends open, gym 90 minutes usually in the evening).

## Non-negotiable: the model plans, it never computes

Free windows, capacity arithmetic and the placement of fixed things are done in
Python and handed over finished. Asked to invent times, a model produces
plausible ones, and a plausible schedule that double-books you is worse than no
schedule. This is the same rule the morning briefing already follows for habit
numbers, for the same reason.

## Non-negotiable: the model is not in the write path

The evening loop reads what was ticked and closes those tasks in Todoist. That
mapping is mechanical - ticked item, close that task - and needs no inference.
The moment a model decides what got done, one bad read closes the wrong thing
and it goes unnoticed for days.

**Only ever mark complete. Never reschedule.** If the due dates move, errors
compound across days and the honest signal is lost - `postponed_count` and days
late are already in the data and are worth more than a tidy list.

## Ordered, not timed

A plan that says "gym at 6, report at 7:30" is wrong by 10am on a normal day,
and a fixed timetable gets abandoned in week two. Fixed commitments get real
times because they genuinely have them. Everything else gets a sequence and an
estimate, placed inside a window.

## The product landscape, checked September 2026

| | API | Cost | Core mechanic |
|---|---|---|---|
| Structured | none | — | The day as a live timeline |
| Sunsama | official MCP server + community TS wrapper | ~$20/mo | Planning ritual with capacity math, shutdown review |
| Motion | none open | $19-34/mo | Constraint solver; re-solves as reality diverges |
| Amazing Marvin | real, documented, key in settings | $96/yr | To-do list with strategies layered on |
| TickTick | Open API v1, OAuth2, free | free | A to-do list with a calendar view - same model as Todoist |
| Google Tasks | official, free | free | List. **`due` is date-only; the time is discarded** |
| Notion | official, free on personal | free | No model at all - you define the shape |

**No free tool has a day-shaped data model.** TickTick and Google Tasks are
lists you would have to fight. Notion imposes nothing, which is why it was the
best of the free options - you build the day view, Notion holds it.

Self-hosting won on the merits: a 15KB page served from the VM loads faster than
Notion's mobile app, the layout is exactly a day, and no third party holds the
plan. The morning job writes into local SQLite rather than calling an API.

## What the prototype already has, and what it is missing

Replicated: the day as a timeline; fixed commitments as un-tickable context;
free windows as containers holding the work placed in them; notes, estimates,
late and postponed chips; habits owed shown as work; bedtime as the last item so
the day visibly ends.

**Three gaps that are fundamental, not edge cases:**

1. **Now-awareness.** The page does not know what time it is. No now-line, no
   "next up", no time left in the current window. This is what makes Structured
   feel alive. Client-side only, an hour of work.
2. **Capacity math.** Both sides are already computed and never compared.
   "4h30 free, 2h placed" or "3h of work in a 2h window". This is Sunsama's
   whole product and it is arithmetic. Highest value per hour of work here.
3. **Replanning.** Ninety minutes behind at 3pm and the plan is fiction. Motion
   re-solves; this would go stale and stop being trusted. Needs an endpoint that
   regenerates from the current time. The hard one, and the one that decides
   whether this is still in use in a month.

Plus one infrastructure gap: **state that follows you between laptop and phone.**
The prototype's ticks are per-browser.

Not worth replicating: drag to reorder, push notifications, calendar write-back.

## What this does that none of them can

They plan from tasks and a calendar. This also knows the habits are behind this
week, that the gym takes ninety minutes and should end three hours before an 11pm
bed, that the Spanish Grand Prix is at 6am and costs two hours, and that the
evening window is 5-11 because of a declared week shape. Motion would schedule
the gym into a slot. It would not know it was owed, or when it should finish.

## Hosting and access

The VM rejects everything inbound except port 22, and Oracle's own security list
would need changing too. Both good options avoid opening anything, because they
make outbound connections:

- **Tailscale** (recommended). Free for personal use. VM, laptop and phone join a
  private network; `tailscale serve` gives HTTPS on a stable name. Nothing is
  exposed to the internet, **so there is no auth to write** - and a page holding
  the day's plan should not sit behind a login written from scratch.
- **Cloudflare Tunnel.** Also free, also no open ports, public hostname, and
  Cloudflare Access adds email-code login. Needs a domain, so about $10/year.

The app itself is small: a standard-library Python HTTP server, one page, SQLite
holding the tick state, a POST when something is checked. Perhaps 150 lines, no
dependencies, within the box's stdlib-only house rule. A web app manifest puts it
on the phone's home screen with an icon, opening full screen.

## Suggested build order

Value first, infrastructure second, the hard thing last. Each phase is useful on
its own and none of it is wasted if the next is never built.

**1. Capacity and placement, still by email.** `briefing.py tasks` already
fetches everything. Add the arithmetic comparing estimates against window
minutes, and have the model place work into named windows. Output stays the 7:30
email. No new infrastructure, immediate value, and it proves the generation is
good before anything is built around it.

**2. The page.** Stdlib HTTP server plus SQLite on the VM. The 7:30 job writes
the plan into SQLite instead of rendering an email; the page serves it; ticking
POSTs back. Tailscale for access. This is where the prototype's HTML becomes real.

**3. Now-awareness and capacity display.** Client-side. Cheap, and it is what
makes the page feel like a tool rather than a document.

**4. Replan.** An endpoint that re-runs generation from the current time with
what is left. The real feature.

**5. Evening sync.** A mechanical job that closes ticked tasks in Todoist.
Complete only. Never reschedule.

## Open questions

- **Lifestyle rules need collecting.** Only two exist so far (eat four hours
  before bed, gym three hours before). The quality of the plan is mostly a
  function of this list.
- **Does the email survive?** Once the page exists, is the 7:30 email a
  duplicate, or the notification that the plan is ready?
- **What happens to a plan that is never opened?** Silently discarded, or does
  it carry forward?
- **Notion remains the fallback** if self-hosting turns out to be more
  maintenance than it is worth. The page design transfers either way.
