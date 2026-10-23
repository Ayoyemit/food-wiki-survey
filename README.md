# Food Preference Wiki Survey

A very small pairwise-comparison survey (AllOurIdeas / wiki-survey style) built from the
IMPACT S2S wiki-survey engine. Participants see two foods at a time and tap the one they
would rather eat. There are three food groups and four foods in each. No roles, no
demographics, no organization- or system-level tagging: food options only.

| Group | Foods |
|---|---|
| Rice dishes | Chicken fried rice · Chicken biryani · Vegetable fried rice · Brown rice with grilled chicken |
| Sandwiches and wraps | Turkey and cheese sandwich · Grilled chicken sandwich · Hummus and vegetable wrap · Tuna salad sandwich |
| Main dishes | Grilled salmon with vegetables · Roast chicken with potatoes · Beef tacos · Vegetable curry with rice |

Pairs are chosen adaptively with the same Bradley-Terry MAP selector as IMPACT S2S
(`algorithm.py`, unchanged), and the rankings are Bradley-Terry strengths. The whole thing
takes a participant about two minutes (18 taps: 6 per group).

## Run it locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open <http://localhost:5000> for the survey and <http://localhost:5000/admin> for the
results dashboard. The SQLite database is created automatically in `instance/` on first run
and the 12 foods are loaded from `data/foods.csv`.

The local admin access code is `admin2026`. That is only the local fallback: a deployed
server uses whatever `ADMIN_CODE` is set to in its environment.

## What participants see

1. Landing page: one paragraph, a consent checkbox, Start. No role selection.
2. Voting page: three group tabs, a question ("Which would you rather eat?"), two food
   cards, "I can't decide", and a progress bar. Left/right arrow keys also vote.
3. After 6 choices in a group a small "group done, next group" prompt appears; after all
   three groups a thank-you. Extra votes are still recorded.

## What the study team sees (`/admin`)

* Rankings per group with a 0–100 score (50 = average within the group), wins, times shown,
  win rate. Refreshes every 15 seconds.
* Participants: one row per session with votes per group and completion flag.
* Can't-decide list and (if enabled) participant suggestions.
* CSV downloads: rankings, every vote, and the full algorithm log (which pair was served,
  why, model state at the time).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_CODE` | `admin2026` (local only) | Admin portal access code. Always set this on a server. |
| `SECRET_KEY` | dev value | Flask session secret. Set a random one on a server. |
| `DATABASE_URL` | `sqlite:///food_survey.db` | Railway/Heroku Postgres URL works as-is. |
| `FOODS_CSV` | `data/foods.csv` | Foods file (`Category,Food`). Edit this to change the options. |
| `VOTES_GOAL_PER_CATEGORY` | `6` | Choices per group before the "group done" prompt. |
| `ALLOW_SUGGESTIONS` | `false` | Let participants suggest a missing food (goes to admin only). |
| `BT_SIGMA2` / `BT_PROXY` | `1.0` / `top_heavy` | Bradley-Terry prior variance and pair-selection proxy. |

To change the foods, edit `data/foods.csv` and delete `instance/food_survey.db` (foods are
loaded only when the table is empty).

## Deploy (Railway, same as IMPACT S2S)

`Procfile`, `runtime.txt` and `mise.toml` are included. Push to a GitHub repo, create a
Railway service from it, add a Postgres database (sets `DATABASE_URL`), and set
`ADMIN_CODE` and `SECRET_KEY` in the service variables.

## Files

```
app.py                  Flask routes: survey pages, participant API, admin API, CSV exports
algorithm.py            Bradley-Terry MAP + adaptive pair selection (from IMPACT S2S, unchanged)
config.py               Environment-variable configuration
models.py               SQLAlchemy tables: foods, sessions, comparisons, cant_decides,
                        suggestions, exposure_counts, algorithm_logs
data/foods.csv          The 12 foods in 3 groups
templates/              index.html (landing), survey.html (voting), admin/login.html, admin/dashboard.html
static/css/style.css    Stylesheet (IMPACT S2S palette)
```

## Tested

Simulated 150 participants (18 votes each, 3% skips) against hidden preference strengths:
the Bradley-Terry ranking recovered the true order in all three groups, all exports and
dashboard views worked, and pages render on desktop and a 390px phone.

MIT License.
