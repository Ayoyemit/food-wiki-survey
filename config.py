import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'food-survey-secret-key-change-in-production')

    # Local development: sqlite (auto-created in ./instance/).
    # Railway/Heroku Postgres: DATABASE_URL is set by the platform.
    _db_url = os.environ.get('DATABASE_URL', 'sqlite:///food_survey.db')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Admin portal access code. The value below is a LOCAL fallback only;
    # any deployed server must set ADMIN_CODE in its environment.
    ADMIN_CODE = os.environ.get('ADMIN_CODE', 'admin2026')

    # CSV with the food options (columns: Category, Food). Loaded on first run.
    FOODS_CSV = os.environ.get('FOODS_CSV', 'data/foods.csv')

    # Bradley-Terry prior variance (higher = less shrinkage toward 0).
    BT_SIGMA2 = float(os.environ.get('BT_SIGMA2', '1.0'))
    # Pair-selection proxy: 'top_heavy' (default) or 'exposure'.
    BT_PROXY = os.environ.get('BT_PROXY', 'top_heavy')

    # Votes per category before the participant is told they have hit the goal.
    # With 4 foods per category there are 6 distinct pairs, so 6 is one full round.
    VOTES_GOAL_PER_CATEGORY = int(os.environ.get('VOTES_GOAL_PER_CATEGORY', '6'))

    # Let participants suggest a food that is missing (reviewed by admin, never
    # shown to other participants automatically). Off by default: Tak asked for
    # food options only.
    ALLOW_SUGGESTIONS = os.environ.get('ALLOW_SUGGESTIONS', 'false').lower() == 'true'
