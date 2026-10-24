"""Small helper for the Seva database.
  python manage.py import   -> reload the database from faq.csv (run this after you edit faq.csv)
  python manage.py list     -> show what is in the database"""
import sys
import db

db.init()
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "import":
    with db.connect() as c:
        print("Loaded", db.import_csv(c), "rows from faq.csv")
elif cmd == "list":
    with db.connect() as c:
        for r in c.execute("SELECT id, venue, keywords, answer_en, is_sample FROM faq ORDER BY venue, id"):
            print(f"{r['id']:>3} [{r['venue']}] {r['keywords']}  ->  {r['answer_en']}" + ("  (sample)" if r["is_sample"] else ""))
else:
    print(__doc__)
