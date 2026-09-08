# Decisions

Why this is built the way it is, including the paths not taken. The README says
what it does; this says why.

## The product is a mirror, not a tracker

The briefing exists to show evidence of who you have been, before the day starts
and has a chance to argue otherwise. It is not a habit app with notifications
attached.

That framing decides most of what follows.

## Streaks were rejected as the metric

The original idea was streak-driven: "my streak is at N weeks, keep going."
That's the wrong metric for a mirror. A streak is binary and brittle - one
missed day reads as zero, so you'd wake up to proof you're failing on exactly
the morning you most need proof you're not.

Everything is measured as **rate against a weekly target** instead. A bad
Tuesday moves the number a few points and the identity survives it. Rolling
30-day window rather than calendar month, so the figure is comparable every day
instead of meaningless on the 2nd.

A once-a-week habit done once a week is met, not 14% complete. Targets are per
habit, and `kind: "avoid"` inverts the metric so the number counts days broken.

## Admission is the filter; rendering is complete

A high bar to get a module in. Once in, everything in it is reported - the
briefing never picks highlights or hides a bad number.

The test for admission: **does it change my state of mind, or does it only
inform me?** Orientation passes. Information fails.

Rejected on that test: news, markets, weather, inbox counts. News in particular
makes you reactive and other-directed, which is the state the briefing exists to
prevent.

## The model narrates; it never computes

A script can only template. "Gym 12/30" is data; "you've been more consistent
this month than last" is interpretation, and no amount of string formatting gets
there.

So: Python computes every number, the model writes prose over finished
statistics, and the prompt forbids it from stating a figure it wasn't given. The
table is printed underneath the paragraph, which means the prose is always
checkable against the numbers it describes.

The model is also handed the raw daily pattern per habit - one character a day -
rather than pre-chewed week-vs-week buckets, so it finds the trend itself.

## Sleep is a target, not an excuse

Short sleep never produces advice to do less today. The reader wants to be well
slept, not excused. It produces a concrete bedtime for tonight, calculated
against when the last calendar event actually ends.

Resting heart rate is deliberately absent from the table and mentioned only when
elevated, so that when you read about it, it means something.

Single-night sleep stage figures are treated as unreliable, because wrist
devices score them at roughly 70% accuracy and underestimate wake time.
Regularity of bedtime is weighted heavily: it predicts mortality better than
duration does.

## Nothing is allowed to cost the morning message

Every external call degrades rather than cancels. A dead calendar feed, a 503
from the model, an expired health token - the briefing still arrives, with less
in it. The one thing that must not happen is silence at 6am.

## Tools considered and rejected

| Option | Why not |
|---|---|
| Streaks (iOS) | No export, no API, no server. Data can only be dragged out by hand through Shortcuts. |
| Habitify | Good app, real REST API, but the API is behind the paid tier. |
| Habitica | Free with a full API, but the RPG layer was unwanted. |
| A custom logging app | Considered a calendar-grid PWA, then a native iOS app. Both were made unnecessary once Todoist proved it could hold habits as recurring tasks. Native would also have needed the same backend anyway, plus $99/year and weekly re-signing. |
| Apple Calendar via CalDAV | Viable, but Google Calendar has a JSON API, a no-auth secret iCal feed, and native Todoist integration. |
| Fitbit Web API | Retired September 2026, and closed to new developer accounts. |

## Things that surprised us

Recurring Todoist tasks never appear in the completed-tasks endpoints -
completing one just advances its due date. The activity log is the only source
that records them.

Google's OAuth apps left in "Testing" status issue refresh tokens that expire
after 7 days, which would silently kill an unattended cron job. Publishing the
app fixes it.

Google computes daily resting heart rate *from sleep*, so it is a sleeping-HR
figure rather than a daytime average - which is the more useful of the two.

## Habits left Todoist

Recurring Todoist tasks looked ideal: you already tap them, and completions are
real events. But a missed day does not create a new instance - the task simply
goes overdue. Miss three days and there is one task, three days late, completable
once. There is no way to record "did it today, not the two days before".

Habits moved to a separate bot with its own evening message and its own Telegram
identity. It owns the database; this reads it. Todoist kept the thing it is good
at, which is tasks.

## The morning split into two messages

One message tried to be two things. Half past six is strategic - who you have
been, and how you slept - and is read in bed. Half past seven is operational -
what today asks of you - and is read standing up. They want different mindsets,
different hours, and as it turned out different mediums.

The 6:30 message goes to Telegram because it is a glance. The 7:30 one goes by
email because it is a document: tasks carry their notes, deadlines, estimates
and postpone counts, and Telegram's fixed-width blocks scroll sideways on a
phone and cap at 4,096 characters.

Casualty of the split: the wind-down line. It read as sleep advice but was
computed from the calendar, and the calendar is no longer in that message.

## Twenty-eight days, and seven

Four whole weeks, so weekday effects cancel and one bad day barely moves the
number. Seven for the momentum figure for the same reason - every window holds
exactly one of each weekday, so it moves when behaviour moves and not when the
window happens to land on a weekend. Three or four days was considered and
rejected: with three days there are only four possible values, and one miss
reads as a collapse.

Both windows clamp to the day tracking started, so both report on whatever
history exists rather than withholding themselves until full.

The seven-day column only appears for habits meant to happen most days, derived
from `target_per_week >= 5` or `kind: avoid` rather than a separate flag.
Meditation at three a week would read 43% on a perfect week, which looks like
failure and is not.

## Targets left the table

They live in the reader's head. Printed, they turned every row into a verdict,
and they are not passed to the model either - given a target, it eventually
writes "you are behind", which is the exact framing this is built to avoid.

## Three prompts, one dataset

Tested against 28 simulated days. A minimal prompt - say what the message is
for, cap the length - produced a hype-bot: bullets, bold, "crushing workouts",
"go win today", and unsolicited advice. Adding voice and honesty rules swung it
the other way into pure recital, five percentages in a row.

Only the long prompt produced something worth reading. The lesson: each block in
it prevents a specific failure the other two demonstrate. Drop "no bullets" and
you get bullets. Add "never invent a number" without "do not read the table
back" and you get a spreadsheet in sentences.

## Sleep: what is worth trusting

Fitbit's stage classification scores a Cohen's kappa of about 0.41 against
polysomnography, and roughly 40% of real deep sleep is misclassified as light
(*SLEEP Advances*, 2025, 62 adults, six devices). Apple Watch scored 0.53 in the
same study, Garmin 0.21.

But sensitivity for detecting sleep at all is 91-93%, and a meta-analysis (JMIR
2019) found that heart-rate-based Fitbits - the generation this one is - show no
significant difference from PSG on total sleep time, wake after sleep onset, or
efficiency. Per-epoch accuracy and night-level accuracy are different things: it
misplaces *which* minutes were awake while getting the *total* about right.

So: total sleep, sleep period, awake minutes, efficiency, sleep start and wake
time are trustworthy at the level of a single night. Deep, REM and light are
trustworthy only as a trend across weeks. Sleep onset latency is discarded
entirely - it is systematically underestimated and mostly measures the watch's
own detection lag.

Two things the numbers do not say on their own. The device cannot see you get
into bed, so its "bed time" is roughly when you fell asleep, and efficiency is
measured against a window that starts there rather than at lights-off - which is
why it reads high against the clinical 85-95%. Trust it for comparing your own
nights, not against a benchmark.

Resting heart rate is calculated `WITH_SLEEP`, from heart rate during the sleep
period rather than a daytime average. That makes it cleaner - no coffee, no
stairs, no standing up - and it is why it rises after drinking, a late meal,
short sleep, or something coming on. A night without sleep data yields no
resting heart rate for that day.

## Sleep compares like with like

A Saturday lie-in is not evidence about a Tuesday. The baseline averages only
nights of the same kind, split by the morning you wake from them: Sunday to
Thursday nights end on a working morning, Friday and Saturday nights do not.
This is the same split `targets_for` already used.

## Google Health has more than is being read

Available today with the scopes already granted, and unused: continuous heart
rate, blood oxygen, heart rate variability, and `daily-heart-rate-variability` -
average HRV, non-REM heart rate, entropy, and deep-sleep RMSSD. HRV is the best
recovery signal a wrist device produces and the one metric that would
independently corroborate the alcohol and sleep habits.

Behind one more consent click (they return 403): steps, distance, active
minutes, VO2 max. Returning nothing: weight, body fat - no scale feeds them.
Not real data types at all: blood pressure, stress, readiness, respiratory rate,
skin temperature, nutrition, hydration.

## CRON_TZ does nothing on Ubuntu

Debian and Ubuntu ship a fork of cron that does not implement `CRON_TZ`. It is a
cronie feature, it is absent from their `crontab(5)`, and the line is ignored
without a warning. The box defaulted to UTC, so every "6:30" job fired at 23:30
local and the morning briefing arrived at night, from first deployment until it
was found.

Set the machine's timezone instead - `timedatectl set-timezone` - and cron times
become genuinely local and follow daylight saving on their own. Nothing in the
Python was ever affected; both files pin their own `ZoneInfo`.

**This lesson is recorded incorrectly in `Newsletter-Digest/BRIEF.md`**, which
still says `CRON_TZ` is not optional. It will reproduce the same bug.

## Not built yet

- **Days off.** Nothing knows about public holidays or leave. On Labor Day the
  system blocks 7am to 5pm as office, offers two short evening windows instead
  of a whole free day, and applies weekday sleep targets. A `days_off` list of
  dates in `week.json`, behaving exactly like a Saturday, covers both holidays
  and PTO - which a subscribed holiday calendar never would. Send times cannot
  follow, since cron cannot read the file.
- **Heart rate**, given the same treatment as sleep above: what the research
  says, which metrics survive it, what belongs in the table.
- **The day sheet**, specified in `day-sheet/BRIEF.md`.
- **Newsletter digest**, specified in `Newsletter-Digest/BRIEF.md`.
- `gemini-3.8-flash` has returned 429 on every call across a full day. The
  fallback chain works, so nothing breaks, but the first model is never reached.
- The GitHub repo description still describes only the Telegram briefing.
- A weekly edition: same data, longer window, deep and REM trends that need a
  week to mean anything.
- A standing intention - one line written on Sunday, echoed each morning. Highest
  value for the least work, since it needs no integration at all.
- People cadence: recurring "call X" tasks already produce the right data.
- Two-way on the briefing itself - replying to log or note something.

## The gym bot is a second bot, not a second feature

The obvious move was handlers on the habits bot - one chat for everything,
which is what "assistant" is supposed to feel like. Rejected on two counts.

One process means a crash while parsing `bench 150` takes the evening habit card
down with it, and the habit card is the half that has to be reliable. And a bot
has one command list: the gym needs enough commands that the habits ones stop
being findable in the menu.

Separate token, separate process, separate database. The cost is a second chat
to open, which is the right thing to pay.

## Weight only; reps are not recorded

Standard advice says a weight-only log misreads progress, because most weeks
you add reps rather than plates. It does not apply here - the reps are fixed
every session, so the only thing that moves is the weight, and asking for a
second number at the rack would be friction buying nothing.

## Weights are signed, so progress is always up

Assisted pull ups are recorded as negative: `-60` is sixty pounds of help, and
progress runs `-60 → -40 → 0`. The alternative was a per-exercise
"lower is better" flag, which puts a branch in every comparison, every chart and
every summary the trend will ever feed. The sign carries the same information
and nothing downstream has to know.

## Per-exercise slash commands were rejected

The first sketch was `/bench 150`. Telegram delivers unregistered commands
anyway, so it would have worked, but it means either a command menu with twenty
entries in it or having to recall an exact slug while standing at the rack -
which is the thing this was built to stop.

Instead the list is printed with numbers and updates are plain messages: `1 150`
by position, `bench 150` by name. Nothing to register, and `/add` can invent an
exercise at the gym without a deploy.

## Nothing is overwritten

There is no current-weight column. Every update appends `(timestamp, exercise,
weight)` and the current weight is a query for the newest row. The trend is
therefore free rather than a feature to be added later, and `/undo` is one
`DELETE`.

`/remove` deactivates instead of deleting, and `/add` of a name used before
wakes the old row rather than inserting a second one - so a lift dropped for six
months rejoins its own history instead of starting a parallel record.