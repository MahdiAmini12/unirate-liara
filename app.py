from flask import Flask, render_template, request, redirect, session, flash, jsonify
import os
import sqlite3
import jdatetime

app = Flask(__name__)
app.secret_key = "secret123"

def get_db():
    conn = sqlite3.connect("/data/ratings.db")
    conn.row_factory = sqlite3.Row
    return conn



def get_most_popular_professor(db):
    total_stats = db.execute("""
        SELECT 
            AVG(( (q1 + (10 - q2) + q3 + q4) / 4.0 )) as avg_score,
            COUNT(*) as total_votes
        FROM Votes v
        JOIN Courses c ON v.course_id = c.id
        WHERE c.category != 'عمومی'
    """).fetchone()

    # تبدیل به مقیاس 5
    C = (total_stats["avg_score"] / 10 * 5) if total_stats["avg_score"] else 0
    m = 5

    professors = db.execute("""
        SELECT 
            p.id, p.name, p.photo_url,
            COUNT(*) as vote_count,
            AVG(q1) as avg_q1,
            AVG(q2) as avg_q2,
            AVG(q3) as avg_q3,
            AVG(q4) as avg_q4
        FROM Professors p
        JOIN Votes v ON p.id = v.professor_id
        JOIN Courses c ON v.course_id = c.id
        WHERE c.category != 'عمومی'
        GROUP BY p.id
    """).fetchall()

    best_professor = None
    best_score = -1

    for prof in professors:
        # مقیاس‌دهی
        avg_q2_inverted = 10 - prof["avg_q2"] if prof["avg_q2"] is not None else 0
        raw_avg = (prof["avg_q1"] + avg_q2_inverted + prof["avg_q3"] + prof["avg_q4"]) / 4

        avg_rating_scaled = (raw_avg / 10) * 5  # مقیاس 5

        n = prof["vote_count"]
        bayesian_score = (n / (n + m)) * avg_rating_scaled + (m / (n + m)) * C

        if bayesian_score > best_score:
            best_score = bayesian_score
            best_professor = dict(prof)

    if best_professor:
        major_info = db.execute("""
            SELECT m.name, COUNT(*) as vote_count
            FROM Votes v
            JOIN Courses c ON v.course_id = c.id
            JOIN CourseMajor cm ON cm.course_id = c.id
            JOIN Majors m ON m.id = cm.major_id
            WHERE v.professor_id = ?
            GROUP BY m.id
            ORDER BY vote_count DESC
            LIMIT 1
        """, (best_professor["id"],)).fetchone()

        if major_info:
            major_name = major_info["name"]
            if major_name.startswith("مهندسی کامپیوتر"):
                major_name = "مهندسی کامپیوتر"
            best_professor["top_major_name"] = major_name
        else:
            best_professor["top_major_name"] = "نامشخص"

    return best_professor, best_score



@app.route("/", methods=["GET", "POST"])
def login():
    db = get_db()
    majors = db.execute("SELECT * FROM Majors").fetchall()

    student_count = db.execute("SELECT COUNT(DISTINCT student_id) as count FROM Students").fetchone()["count"]
    comment_count = db.execute("SELECT COUNT(*) as count FROM Votes").fetchone()["count"]
    professor_count = db.execute("SELECT COUNT(*) as count FROM Professors").fetchone()["count"]
    course_count = db.execute("SELECT COUNT(*) as count FROM Courses").fetchone()["count"]

    # دریافت استاد محبوب و امتیازش
    popular_professor, popular_score = get_most_popular_professor(db)

    if request.method == "POST":
        student_id = request.form["student_id"]
        major_id = request.form["major_id"]

        existing_student = db.execute(
            "SELECT major_id FROM Students WHERE student_id = ?",
            (student_id,)
        ).fetchone()

        if existing_student:
            current_major_id = existing_student["major_id"]
            if current_major_id != int(major_id):
                session["error_message"] = "شما قبلاً رشته دیگری انتخاب کرده‌اید و نمی‌توانید رشته جدید انتخاب کنید."
                db.close()
                return redirect("/")

        db.execute("""
            INSERT INTO Students (student_id, major_id) VALUES (?, ?)
            ON CONFLICT(student_id) DO UPDATE SET major_id=excluded.major_id
        """, (student_id, major_id))
        db.commit()

        session["student_id"] = student_id
        session["major_id"] = major_id
        db.close()
        return redirect("/courses")

    error_message = session.pop("error_message", None)
    db.close()

    return render_template(
        "login.html",
        majors=majors,
        error_message=error_message,
        student_count=student_count,
        comment_count=comment_count,
        professor_count=professor_count,
        course_count=course_count,
        popular_professor=popular_professor,
        popular_score=popular_score
    )



@app.route("/courses")
def courses():
    if "student_id" not in session:
        return redirect("/")

    category_filter = request.args.get("category")
    search_query = request.args.get("q", "").strip()

    db = get_db()

    # پایه کوئری
    query = """
        SELECT c.id, c.name, c.category, GROUP_CONCAT(p.name, ', ') as professors
        FROM Courses c
        JOIN CourseMajor cm ON c.id = cm.course_id
        LEFT JOIN CourseProfessor cp ON c.id = cp.course_id
        LEFT JOIN Professors p ON cp.professor_id = p.id
        WHERE cm.major_id = ?
    """
    params = [session["major_id"]]

    # فیلتر دسته بندی
    if category_filter:
        query += " AND c.category = ?"
        params.append(category_filter)

    # فیلتر جستجو در درس یا استاد
    if search_query:
        query += " AND (c.name LIKE ? OR p.name LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%"])

    query += " GROUP BY c.id"

    courses = db.execute(query, params).fetchall()
    db.close()

    return render_template("courses.html", courses=courses, selected_category=category_filter, search_query=search_query)

# API جستجوی اتوکامپلیت
@app.route("/search_suggestions")
def search_suggestions():
    if "student_id" not in session:
        return jsonify([])

    category_filter = request.args.get("category")
    q = request.args.get("q", "").strip()

    if not q:
        return jsonify([])

    db = get_db()

    # جستجو در نام درس و استادها
    query = """
        SELECT DISTINCT c.name AS course_name, p.name AS professor_name, c.category
        FROM Courses c
        JOIN CourseMajor cm ON c.id = cm.course_id
        LEFT JOIN CourseProfessor cp ON c.id = cp.course_id
        LEFT JOIN Professors p ON cp.professor_id = p.id
        WHERE cm.major_id = ?
    """
    params = [session["major_id"]]

    if category_filter:
        query += " AND c.category = ?"
        params.append(category_filter)

    query += " AND (c.name LIKE ? OR p.name LIKE ?)"
    params.extend([f"%{q}%", f"%{q}%"])

    query += " LIMIT 10"

    results = db.execute(query, params).fetchall()
    db.close()

    suggestions = []
    for row in results:
        # اگر نام درس با q تطابق داشت
        if row["course_name"] and q in row["course_name"]:
            suggestions.append({"type": "درس", "value": row["course_name"]})
        # اگر نام استاد با q تطابق داشت
        if row["professor_name"] and q in row["professor_name"]:
            suggestions.append({"type": "استاد", "value": row["professor_name"]})

    # حذف موارد تکراری (اگر هم استاد و هم درس یکی بود)
    seen = set()
    unique_suggestions = []
    for s in suggestions:
        key = (s["type"], s["value"])
        if key not in seen:
            seen.add(key)
            unique_suggestions.append(s)

    return jsonify(unique_suggestions)


@app.route("/rate/<int:course_id>", methods=["GET", "POST"])
def rate(course_id):
    if "student_id" not in session:
        return redirect("/")

    db = get_db()

    # 📅 ساخت رشته‌ای به فرمت 06/1404
    today = jdatetime.date.today()
    month = f"{today.month:02d}/{today.year}"

    if request.method == "POST":
        professor_id = int(request.form["professor"])
        q1 = int(request.form["q1"])
        q2 = int(request.form["q2"])
        q3 = int(request.form["q3"])
        q4 = int(request.form["q4"])
        comment = request.form["comment"]

        # ✅ بررسی اینکه آیا این دانشجو در همین ماه برای این درس و استاد رأی داده یا نه
        existing = db.execute("""
            SELECT * FROM Votes
            WHERE student_id = ? AND course_id = ? AND professor_id = ? AND month = ?
        """, (session["student_id"], course_id, professor_id, month)).fetchone()

        if existing:
            db.close()
            return render_template("error.html", message="شما در این ماه برای این درس و استاد نظرسنجی کرده‌اید.")

        # درج رأی جدید
        db.execute("""
            INSERT INTO Votes (student_id, course_id, professor_id, q1, q2, q3, q4, comment, month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session["student_id"], course_id, professor_id, q1, q2, q3, q4, comment, month))
        db.commit()
        db.close()
        return redirect("/courses")

    course = db.execute("SELECT id, name FROM Courses WHERE id = ?", (course_id,)).fetchone()
    professors = db.execute("""
        SELECT p.id, p.name FROM Professors p
        JOIN CourseProfessor cp ON cp.professor_id = p.id
        WHERE cp.course_id = ?
    """, (course_id,)).fetchall()

    db.close()
    return render_template("rate.html", course=course, professors=professors)

@app.route("/stats/<int:course_id>", methods=["GET", "POST"])
def stats(course_id):
    db = get_db()
    course = db.execute("SELECT id, name FROM Courses WHERE id = ?", (course_id,)).fetchone()

    professors = db.execute("""
        SELECT p.id, p.name FROM Professors p
        JOIN CourseProfessor cp ON cp.professor_id = p.id
        WHERE cp.course_id = ?
    """, (course_id,)).fetchall()

    selected_professor_name = None
    stats = None
    comments = []
    compare_results = None
    selected_professors = []

    # --- ابتدا محاسبه C و m برای Bayesian Average ---

    # محاسبه امتیاز کل (کل رای‌ها و میانگین امتیاز کل) برای همه اساتید این دوره
    all_stats = db.execute("""
        SELECT 
            professor_id,
            COUNT(*) as vote_count,
            AVG(q1 + (10 - q2) + q3 + q4) as avg_total_score
        FROM Votes
        WHERE course_id = ?
        GROUP BY professor_id
    """, (course_id,)).fetchall()

    if all_stats and len(all_stats) > 0:
        # C = میانگین کل امتیاز کل تمام اساتید (وزن‌دار بر اساس تعداد رای)
        total_weighted_score = sum(row["avg_total_score"] * row["vote_count"] for row in all_stats)
        total_votes = sum(row["vote_count"] for row in all_stats)
        C = total_weighted_score / total_votes if total_votes > 0 else 0

        # m = میانگین تعداد رای‌ها
        m = total_votes / len(all_stats)
    else:
        # اگر داده‌ای نبود مقدار پیش‌فرض
        C = 25  # چون بازه 4 سوال 1 تا 10، حد وسط حدود 25 است (میانگین 6.25 در هر سوال)
        m = 10

    # تابع محاسبه Bayesian Average امتیاز کل نرمال شده
    def bayesian_score(avg_score, vote_count):
        # avg_score دامنه 4 تا 40 (4 سوال * 1 تا 10)
        # نرمال سازی به 1 تا 10 با تقسیم بر 4
        norm_avg = avg_score / 4.0

        bayes_avg = ((C/4.0)*m + norm_avg * vote_count) / (m + vote_count)
        # محدود کردن بین 1 تا 10
        return max(1, min(10, bayes_avg))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "compare":
            selected_professors = request.form.getlist("professors")
            if selected_professors:
                prof_ids = [int(p) for p in selected_professors]
                compare_results = []
                for pid in prof_ids:
                    prof = db.execute("SELECT id, name FROM Professors WHERE id = ?", (pid,)).fetchone()
                    if prof:
                        stat = db.execute("""
                            SELECT 
                                AVG(q1) as avg_q1, AVG(q2) as avg_q2, AVG(q3) as avg_q3, AVG(q4) as avg_q4,
                                COUNT(*) as vote_count,
                                AVG(q1 + (10 - q2) + q3 + q4) as avg_total_score
                            FROM Votes WHERE course_id = ? AND professor_id = ?
                        """, (course_id, pid)).fetchone()

                        total_score = bayesian_score(stat["avg_total_score"], stat["vote_count"]) if stat["vote_count"] > 0 else 0

                        compare_results.append({
                            "id": prof["id"],
                            "name": prof["name"],
                            "avg_q1": stat["avg_q1"],
                            "avg_q2": stat["avg_q2"],
                            "avg_q3": stat["avg_q3"],
                            "avg_q4": stat["avg_q4"],
                            # vote_count حذف شد یا می‌ماند ولی در نمایش جایگزین نمی‌شود
                            "total_score": total_score
                        })
            else:
                compare_results = None

        elif action == "view_single":
            professor_id = request.form.get("professors")
            if not professor_id:
                professor_id = request.form.get("professor")
            if professor_id:
                professor_id = int(professor_id)
                prof = db.execute("SELECT id, name FROM Professors WHERE id = ?", (professor_id,)).fetchone()
                if prof:
                    selected_professor_name = prof["name"]
                    stats = db.execute("""
                        SELECT 
                            AVG(q1) as avg_q1, AVG(q2) as avg_q2, AVG(q3) as avg_q3, AVG(q4) as avg_q4,
                            COUNT(*) as vote_count,
                            AVG(q1 + (10 - q2) + q3 + q4) as avg_total_score
                        FROM Votes WHERE course_id = ? AND professor_id = ?
                    """, (course_id, professor_id)).fetchone()

                    comments = db.execute("""
                        SELECT comment FROM Votes
                        WHERE course_id = ? AND professor_id = ? AND comment IS NOT NULL AND comment != ''
                    """, (course_id, professor_id)).fetchall()

                    if stats["vote_count"] > 0:
                        stats = dict(stats)
                        stats["total_score"] = bayesian_score(stats["avg_total_score"], stats["vote_count"])
                    else:
                        stats = dict(stats)
                        stats["total_score"] = 0

    db.close()
    return render_template(
        "stats.html",
        course=course,
        professors=professors,
        stats=stats,
        comments=comments,
        selected_professor_name=selected_professor_name,
        compare_results=compare_results,
        selected_professors=[int(p) for p in selected_professors] if selected_professors else []
    )



@app.route("/support", methods=["GET", "POST"])
def support():
    if request.method == "POST":
        student_id = request.form["student_id"].strip()
        major_text = request.form["major_text"].strip()
        message = request.form["message"].strip()

        if not student_id or not major_text or not message:
            return render_template("support.html", error="لطفاً همه فیلدها را پر کنید.")

        db = get_db()
        db.execute("""
            INSERT INTO SupportMessages (student_id, major_text, message)
            VALUES (?, ?, ?)
        """, (student_id, major_text, message))
        db.commit()
        db.close()
        return render_template("support.html", success="پیام شما با موفقیت ارسال شد.")

    return render_template("support.html")



if __name__ == "__main__":
    # app.run(debug=True)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)