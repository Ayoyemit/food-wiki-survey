from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
import uuid

db = SQLAlchemy()


def _now():
    return datetime.now(timezone.utc)


class Food(db.Model):
    """A food option that participants compare in pairs."""
    __tablename__ = 'foods'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_now)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'category': self.category}


class Session(db.Model):
    """One browser visit = one participant session. No roles, no demographics."""
    __tablename__ = 'sessions'

    id = db.Column(db.String(50), primary_key=True)
    consented = db.Column(db.Boolean, default=True)
    started_at = db.Column(db.DateTime, default=_now)
    ended_at = db.Column(db.DateTime, nullable=True)
    last_activity = db.Column(db.DateTime, default=_now)

    comparisons = db.relationship('Comparison', backref='session', lazy='dynamic')

    @staticmethod
    def generate_id():
        return f"p_{uuid.uuid4().hex[:10]}"

    @property
    def duration_seconds(self):
        end = self.ended_at or self.last_activity
        if end and self.started_at:
            return (end - self.started_at).total_seconds()
        return 0


class Comparison(db.Model):
    """One pairwise vote."""
    __tablename__ = 'comparisons'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(50), db.ForeignKey('sessions.id'), nullable=False)
    winner_id = db.Column(db.Integer, db.ForeignKey('foods.id'), nullable=False)
    loser_id = db.Column(db.Integer, db.ForeignKey('foods.id'), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=_now)

    winner = db.relationship('Food', foreign_keys=[winner_id])
    loser = db.relationship('Food', foreign_keys=[loser_id])


class CantDecide(db.Model):
    """Recorded when a participant skips a pair."""
    __tablename__ = 'cant_decides'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(50), db.ForeignKey('sessions.id'), nullable=False)
    food_a_id = db.Column(db.Integer, db.ForeignKey('foods.id'), nullable=False)
    food_b_id = db.Column(db.Integer, db.ForeignKey('foods.id'), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=_now)

    food_a = db.relationship('Food', foreign_keys=[food_a_id])
    food_b = db.relationship('Food', foreign_keys=[food_b_id])


class Suggestion(db.Model):
    """Optional participant-suggested food (only if ALLOW_SUGGESTIONS=true)."""
    __tablename__ = 'suggestions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(50), db.ForeignKey('sessions.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)


class ExposureCount(db.Model):
    """How many times each food has been shown (drives the adaptive pair selection)."""
    __tablename__ = 'exposure_counts'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    food_id = db.Column(db.Integer, db.ForeignKey('foods.id'), nullable=False, unique=True)
    count = db.Column(db.Integer, default=0)


class AlgorithmLog(db.Model):
    """Audit trail for every pair the algorithm served."""
    __tablename__ = 'algorithm_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=_now)
    focal_id = db.Column(db.Integer, nullable=True)
    opponent_id = db.Column(db.Integer, nullable=True)
    displayed_left_id = db.Column(db.Integer, nullable=False)
    displayed_right_id = db.Column(db.Integer, nullable=False)
    was_cold_start = db.Column(db.Boolean, default=False)
    num_comparisons_used = db.Column(db.Integer, nullable=True)
    details_json = db.Column(db.Text, nullable=True)   # strengths, exposures, weights
    outcome = db.Column(db.String(20), nullable=True)  # 'vote', 'cant_decide', or null
    winner_id = db.Column(db.Integer, nullable=True)
