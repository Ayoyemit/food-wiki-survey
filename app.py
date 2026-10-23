"""
Food Preference Wiki Survey
---------------------------
A deliberately small pairwise-comparison survey (AllOurIdeas style).
Participants pick which of two foods they would rather eat, within three
food categories. No roles, no demographics, no organization-level tagging.

Pairs are chosen adaptively with the Bradley-Terry MAP selector from the
IMPACT S2S wiki survey (algorithm.py), and rankings are BT strengths.
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response
from config import Config
from models import (db, Food, Session as UserSession, Comparison, CantDecide,
                    Suggestion, ExposureCount, AlgorithmLog)
from algorithm import AdaptivePairSelector, get_bt_score
from datetime import datetime, timezone
import csv
import json
import os
from io import StringIO
import numpy as np

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

pair_selector = AdaptivePairSelector(
    sigma2=app.config['BT_SIGMA2'],
    proxy=app.config['BT_PROXY'],
)

GOAL = app.config['VOTES_GOAL_PER_CATEGORY']
ALLOW_SUGGESTIONS = app.config['ALLOW_SUGGESTIONS']


# ─── Data loading ───────────────────────────────────────────────────────────────

def load_foods_from_csv(path):
    """Load foods from CSV (Category, Food) the first time the DB is empty."""
    if Food.query.count() > 0:
        return
    if not os.path.exists(path):
        print(f'[FoodSurvey] CSV not found at {path}; no foods loaded')
        return
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        n = 0
        for row in reader:
            name = (row.get('Food') or '').strip()
            cat = (row.get('Category') or '').strip()
            if not name or not cat:
                continue
            db.session.add(Food(name=name, category=cat))
            n += 1
    db.session.commit()
    print(f'[FoodSurvey] Loaded {n} foods from {path}')


def category_order():
    """Categories in the order they first appear in the CSV/DB."""
    seen = []
    for food in Food.query.filter_by(is_active=True).order_by(Food.id).all():
        if food.category not in seen:
            seen.append(food.category)
    return seen


with app.app_context():
    db.create_all()
    load_foods_from_csv(app.config['FOODS_CSV'])
    print(f'[FoodSurvey] Categories: {category_order()}')
    print(f'[FoodSurvey] Database backend: {db.engine.dialect.name}')


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _json_safe(obj):
    def _default(o):
        if isinstance(o, np.generic):
            return o.item()
        raise TypeError(type(o))
    return json.dumps(obj or {}, default=_default)


def session_counts(sid):
    return {c: Comparison.query.filter_by(session_id=sid, category=c).count()
            for c in category_order()}


def comparisons_for(category, food_ids):
    idset = set(food_ids)
    rows = (db.session.query(Comparison.winner_id, Comparison.loser_id)
            .filter(Comparison.category == category).all())
    return [(w, l) for w, l in rows if w in idset and l in idset]


def exposures_for(food_ids):
    exp = {fid: 0 for fid in food_ids}
    rows = (db.session.query(ExposureCount.food_id, ExposureCount.count)
            .filter(ExposureCount.food_id.in_(food_ids)).all())
    for fid, cnt in rows:
        exp[fid] = int(cnt or 0)
    return exp


def bump_exposure(*food_ids):
    for fid in food_ids:
        ec = ExposureCount.query.filter_by(food_id=int(fid)).first()
        if ec:
            ec.count += 1
        else:
            db.session.add(ExposureCount(food_id=int(fid), count=1))


def touch_session(sid):
    s = UserSession.query.get(sid)
    if s:
        s.last_activity = _now()


def no_cache(resp):
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp


# ─── Pages ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return no_cache(app.make_response(render_template('index.html')))


@app.route('/survey')
def survey():
    sid = session.get('session_id')
    if not sid or not UserSession.query.get(sid):
        return redirect(url_for('index'))
    cats = category_order()
    if not cats:
        return 'No foods loaded. Check data/foods.csv.', 500
    return no_cache(app.make_response(render_template(
        'survey.html', categories=cats, goal=GOAL,
        allow_suggestions=ALLOW_SUGGESTIONS)))


@app.route('/admin')
def admin_gate():
    if session.get('is_admin'):
        return redirect('/admin/dashboard')
    return no_cache(app.make_response(render_template('admin/login.html')))


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect('/admin')
    return no_cache(app.make_response(render_template(
        'admin/dashboard.html', categories=category_order())))


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect('/admin')


# ─── Participant API ────────────────────────────────────────────────────────────

@app.route('/api/start-session', methods=['POST'])
def start_session():
    old_id = session.get('session_id')
    if old_id:
        old = UserSession.query.get(old_id)
        if old and not old.ended_at:
            old.ended_at = _now()
    sid = UserSession.generate_id()
    db.session.add(UserSession(id=sid, consented=True))
    db.session.commit()
    session['session_id'] = sid
    return jsonify({'session_id': sid})


@app.route('/api/get-pair', methods=['POST'])
def get_pair():
    sid = session.get('session_id')
    if not sid:
        return jsonify({'error': 'No active session'}), 400
    data = request.json or {}
    category = data.get('category')
    foods = Food.query.filter_by(is_active=True, category=category).all()
    if len(foods) < 2:
        return jsonify({'error': 'Not enough foods in this category'}), 400
    food_map = {f.id: f for f in foods}
    ids = list(food_map.keys())

    try:
        log = pair_selector.select_pair(ids, comparisons_for(category, ids), exposures_for(ids))
        if log is None:
            return jsonify({'error': 'Could not select a pair'}), 500
        left, right = log['displayed_left_id'], log['displayed_right_id']
        bump_exposure(left, right)
        entry = AlgorithmLog(
            session_id=sid, category=category,
            focal_id=log.get('focal_id'), opponent_id=log.get('opponent_id'),
            displayed_left_id=left, displayed_right_id=right,
            was_cold_start=bool(log.get('was_cold_start')),
            num_comparisons_used=log.get('num_comparisons'),
            details_json=_json_safe({
                'proxy': log.get('proxy_type'),
                'focal_sampling_prob': log.get('focal_sampling_prob'),
                'opponent_strength_diff': log.get('opponent_strength_diff'),
                'map_strengths': log.get('map_strengths'),
                'exposure_counts': log.get('exposure_counts'),
                'uncertainty_weights': log.get('uncertainty_weights'),
            }),
        )
        db.session.add(entry)
        touch_session(sid)
        db.session.commit()
        return jsonify({
            'food_a': food_map[left].to_dict(),
            'food_b': food_map[right].to_dict(),
            'algo_log_id': entry.id,
        })
    except Exception as exc:
        db.session.rollback()
        print(f'[FoodSurvey] get_pair error: {exc}')
        return jsonify({'error': 'Could not load a pair. Please try again.'}), 500


@app.route('/api/vote', methods=['POST'])
def vote():
    sid = session.get('session_id')
    if not sid:
        return jsonify({'error': 'No active session'}), 400
    data = request.json or {}
    winner_id, loser_id, category = data.get('winner_id'), data.get('loser_id'), data.get('category')
    if not all([winner_id, loser_id, category]):
        return jsonify({'error': 'Missing data'}), 400
    w, l = Food.query.get(winner_id), Food.query.get(loser_id)
    if not w or not l or w.category != category or l.category != category:
        return jsonify({'error': 'Invalid pair'}), 400

    db.session.add(Comparison(session_id=sid, winner_id=w.id, loser_id=l.id, category=category))
    entry = AlgorithmLog.query.get(data.get('algo_log_id') or 0)
    if entry:
        entry.outcome, entry.winner_id = 'vote', w.id
    touch_session(sid)
    db.session.commit()
    return jsonify({'success': True, 'counts': session_counts(sid)})


@app.route('/api/cant-decide', methods=['POST'])
def cant_decide():
    sid = session.get('session_id')
    if not sid:
        return jsonify({'error': 'No active session'}), 400
    data = request.json or {}
    db.session.add(CantDecide(session_id=sid, food_a_id=data.get('food_a_id'),
                              food_b_id=data.get('food_b_id'), category=data.get('category')))
    entry = AlgorithmLog.query.get(data.get('algo_log_id') or 0)
    if entry:
        entry.outcome = 'cant_decide'
    touch_session(sid)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/suggest', methods=['POST'])
def suggest():
    if not ALLOW_SUGGESTIONS:
        return jsonify({'error': 'Suggestions are turned off'}), 403
    sid = session.get('session_id')
    if not sid:
        return jsonify({'error': 'No active session'}), 400
    data = request.json or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'Please type a food'}), 400
    db.session.add(Suggestion(session_id=sid, text=text[:500], category=data.get('category')))
    touch_session(sid)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/counts')
def counts():
    sid = session.get('session_id')
    return jsonify(session_counts(sid) if sid else {})


@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    sid = session.get('session_id')
    if sid:
        touch_session(sid)
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/end-session', methods=['POST'])
def end_session():
    sid = session.get('session_id')
    if sid:
        s = UserSession.query.get(sid)
        if s:
            s.ended_at = _now()
            db.session.commit()
    return jsonify({'ok': True})


# ─── Admin API ──────────────────────────────────────────────────────────────────

def require_admin():
    return None if session.get('is_admin') else (jsonify({'error': 'Unauthorized'}), 401)


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json or {}
    if data.get('code') == app.config['ADMIN_CODE']:
        session['is_admin'] = True
        return jsonify({'success': True, 'redirect': '/admin/dashboard'})
    return jsonify({'error': 'Invalid access code'}), 401


def rankings_for(category):
    foods = Food.query.filter_by(is_active=True, category=category).order_by(Food.id).all()
    ids = [f.id for f in foods]
    fmap = {f.id: f for f in foods}
    comps = comparisons_for(category, ids)
    result = pair_selector.compute_rankings(ids, comps)
    # BT fixes one food's strength at 0 for identification; re-centre so that
    # 50 really means "average within this group".
    betas = [b for _, b in result['rankings']]
    mean_beta = sum(betas) / len(betas) if betas else 0.0
    out = []
    for fid, beta in result['rankings']:
        beta = beta - mean_beta
        wins = sum(1 for w, _ in comps if w == fid)
        apps = sum(1 for w, l in comps if fid in (w, l))
        out.append({
            'id': fid, 'name': fmap[fid].name, 'category': category,
            'score': get_bt_score(beta), 'beta': round(beta, 4),
            'wins': wins, 'appearances': apps,
            'win_rate': round(100 * wins / apps, 1) if apps else None,
        })
    for i, r in enumerate(out, 1):
        r['rank'] = i
    return {'category': category, 'rankings': out, 'votes': len(comps),
            'converged': result.get('converged', True)}


@app.route('/api/admin/summary')
def admin_summary():
    err = require_admin()
    if err:
        return err
    sessions = UserSession.query.all()
    with_votes = sum(1 for s in sessions if s.comparisons.count() > 0)
    durations = [s.duration_seconds for s in sessions if s.comparisons.count() > 0]
    return jsonify({
        'total_foods': Food.query.filter_by(is_active=True).count(),
        'total_votes': Comparison.query.count(),
        'total_cant_decide': CantDecide.query.count(),
        'total_sessions': len(sessions),
        'sessions_with_votes': with_votes,
        'median_duration_seconds': float(np.median(durations)) if durations else 0,
        'goal_per_category': GOAL,
        'categories': [rankings_for(c) for c in category_order()],
        'suggestions_enabled': ALLOW_SUGGESTIONS,
    })


@app.route('/api/admin/participants')
def admin_participants():
    err = require_admin()
    if err:
        return err
    cats = category_order()
    rows = []
    for s in UserSession.query.order_by(UserSession.started_at.desc()).all():
        counts = session_counts(s.id)
        rows.append({
            'id': s.id,
            'started_at': s.started_at.isoformat() if s.started_at else None,
            'duration_seconds': s.duration_seconds,
            'total_votes': sum(counts.values()),
            'counts': counts,
            'complete': all(counts.get(c, 0) >= GOAL for c in cats),
        })
    return jsonify({'participants': rows})


@app.route('/api/admin/cant-decides')
def admin_cant_decides():
    err = require_admin()
    if err:
        return err
    rows = CantDecide.query.order_by(CantDecide.created_at.desc()).all()
    return jsonify({'items': [{
        'id': r.id, 'session_id': r.session_id, 'category': r.category,
        'food_a': r.food_a.name if r.food_a else r.food_a_id,
        'food_b': r.food_b.name if r.food_b else r.food_b_id,
        'created_at': r.created_at.isoformat(),
    } for r in rows]})


@app.route('/api/admin/suggestions')
def admin_suggestions():
    err = require_admin()
    if err:
        return err
    rows = Suggestion.query.order_by(Suggestion.created_at.desc()).all()
    return jsonify({'items': [{'id': r.id, 'text': r.text, 'category': r.category,
                               'created_at': r.created_at.isoformat()} for r in rows]})


@app.route('/api/admin/export/<kind>.csv')
def admin_export(kind):
    err = require_admin()
    if err:
        return err
    buf = StringIO()
    w = csv.writer(buf)
    if kind == 'rankings':
        w.writerow(['category', 'rank', 'food', 'bt_score', 'beta', 'wins', 'appearances', 'win_rate'])
        for c in category_order():
            for r in rankings_for(c)['rankings']:
                w.writerow([c, r['rank'], r['name'], r['score'], r['beta'], r['wins'],
                            r['appearances'], r['win_rate']])
    elif kind == 'votes':
        w.writerow(['comparison_id', 'session_id', 'category', 'winner', 'loser', 'created_at'])
        for cmp_ in Comparison.query.order_by(Comparison.id).all():
            w.writerow([cmp_.id, cmp_.session_id, cmp_.category, cmp_.winner.name,
                        cmp_.loser.name, cmp_.created_at.isoformat()])
    elif kind == 'algorithm_logs':
        w.writerow(['log_id', 'session_id', 'category', 'timestamp', 'cold_start', 'focal_id',
                    'opponent_id', 'left_id', 'right_id', 'num_comparisons_used', 'outcome',
                    'winner_id', 'details_json'])
        for e in AlgorithmLog.query.order_by(AlgorithmLog.id).all():
            w.writerow([e.id, e.session_id, e.category, e.timestamp.isoformat(), e.was_cold_start,
                        e.focal_id, e.opponent_id, e.displayed_left_id, e.displayed_right_id,
                        e.num_comparisons_used, e.outcome, e.winner_id, e.details_json])
    else:
        return jsonify({'error': 'Unknown export'}), 404
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=food_survey_{kind}.csv'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG') == '1')
