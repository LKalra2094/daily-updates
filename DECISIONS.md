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

## Not built yet

- A weekly edition: same data, longer window, deep and REM trends that need a
  week to mean anything.
- A standing intention - one line written on Sunday, echoed each morning. Highest
  value for the least work, since it needs no integration at all.
- People cadence: recurring "call X" tasks already produce the right data.
- Two-way on the briefing itself - replying to log or note something.
