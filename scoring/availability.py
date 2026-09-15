"""Today's date and the start date, in the one form models get right.

Every prompt in this repository that can mention a date imports `note()` from
here. That is not tidiness - it is the fix for a specific, repeated failure.

A model's sense of "now" sits near its training cutoff, so an earliest start of
2027-03-01 gets reported as over a year away when it is about five months. Worse,
supplying today's date alone does not fix it: told plainly that today is
2026-09-10, one model still answered "rund 18 Monate" for a March 2027 start, and
another wrote "10.09.2026 waere Heute" and then used its own sense of the present
anyway. The supplied date contradicts the learned one, so it hedges and falls
back on its prior.

What does work is removing the arithmetic entirely. `availability_note()` states
the gap already computed, in days and months, and forbids the model from working
out any duration of its own. There is then nothing to calculate and nothing to
disagree with.

A prompt that omits this block is not merely missing context; it will
confidently state a wrong number to an employer. The pitch prompt did, which is
how a five-month wait reached a draft as seventeen months.
"""

import logging
from datetime import datetime, timezone


def note() -> str:
    """The availability block, or today's date if the profile cannot be read.

    The fallback matters: this is imported by the tailoring package, which must
    keep working for someone who has no candidate profile at all. Losing the
    start date is survivable; losing the date rule silently is not, so the rule
    survives the fallback.
    """
    try:
        from scoring.score_jobs import availability_note

        return availability_note()
    except Exception as exc:  # noqa: BLE001 - a missing profile must not stop a run
        logging.debug("Availability note unavailable (%s); falling back to today.", exc)
        today = datetime.now(timezone.utc).date().isoformat()
        return (f"Today's date is {today}. Judge every date by counting from it, "
                "never from your own sense of the present. Never state how many "
                "months or years away a date is.")
