import datetime
from psycodict import DelayCommit
from .app import send_email, livesite
from .utils import current_term, current_year
from .dbwrapper import getdb, count_rows

group_preferences = [ 'start', 'style', 'forum', 'size' ]

new_group_subject = "Say hello to your pset partners in {class_number}!"

new_group_email = """
Greetings!  You have been matched with a pset group in <b>{class_number}</b>.<br>
To learn more about your group and its members please visit<br><br>

&nbsp;&nbsp;{url}<br><br>

We encourage you to reach out to your new group today.<br>
You can use the "email group" button on pset partners to do this.
"""

unmatched_only = """
We were not able to match you with a pset group in <b>{class_number}</b> because there were not enough students in the match pool.
We encourage you to visit<br><br>

&nbsp;&nbsp;{url}<br><br>

and either join a public group, or click the "match me asap" button and we will try to put you into an existing group.
"""

unmatched_requirement = """
We were not able to match you with a pset group in <b>{class_number}</b> because we were unable to satisfy one of the preferences
you marked as "required".  If you are still want to join a pset group for this class we encourage you to visit<br><br>

&nbsp;&nbsp;{url}<br><br>

and either join a public group or weaken the strength your required preferences in this class to "strongly preferred"
and click the "match me asap" button.
"""

def student_url(class_number, forcelive=False):
    url = "https://psetpartners.mit.edu/student" if (livesite() or forcelive) else "https://psetpartners-test.mit.edu/student"
    return url if not class_number else url + "/" + class_number

def generate_group_name(class_id, year=current_year(), term=current_term()):
    db = getdb()
    S = { g for g in db.groups.search({'class_id': class_id}, projection='group_name') }
    A = { g.split(' ')[0] for g in S }
    N = { g.split(' ')[1] for g in S }
    acount = count_rows('positive_adjectives')
    ncount = count_rows('plural_nouns')
    while True:
        a = db.positive_adjectives.random({})
        if 2*len(A) < acount and a in A:
            continue
        n = db.plural_nouns.random({'firstletter':a[0]})
        if 4*len(N) < ncount and n in N:
            continue
        name = a.capitalize() + " " + n.capitalize()
        if db.groups.lucky({'group_name': name, 'year': year, 'term': term}):
            continue
        return name

def create_group (class_id, kerbs, match_run=0, group_name='', forcelive=False):
    from .student import max_size_from_prefs, email_address, signature, log_event

    db = getdb(forcelive)
    c = db.classes.lucky({'id': class_id})

    g = { 'class_id': class_id, 'year': c['year'], 'term': c['term'], 'class_number': c['class_number'] }
    g['visibility'] = 2  # unlisted by default
    g['creator'] = ''    # system created
    g['editors'] = []    # everyone can edit

    students = [db.students.lookup(kerb, projection=['kerb','email','preferences', 'id']) for kerb in kerbs]
    g['preferences'] = {}
    for p in group_preferences:
        v = { s['preferences'][p] for s in students if p in s['preferences'] }
        if len(v) == 1:
            g['preferences'][p] = list(v)[0]
    g['max'] = max_size_from_prefs(g['preferences'])
    g['match_run'] = match_run

    with DelayCommit(db):
        g['group_name'] = group_name if group_name else generate_group_name(class_id, c['year'], c['term'])
        print("creating group %s with members %s" % (g['group_name'], kerbs))
        # sanity check
        assert all([db.classlist.lucky({'class_id': class_id, 'student_id': s['id']},projection='status')==5 for s in students])
        db.groups.insert_many([g])
        gs = [{'class_id': class_id, 'group_id': g['id'], 'student_id': s['id'], 'kerb': s['kerb'],
               'class_number': c['class_number'], 'year': c['year'], 'term': c['term']} for s in students]
        db.grouplist.insert_many(gs)
        now = datetime.datetime.now()
        for s in students:
            db.classlist.update({'class_id': class_id, 'student_id': s['id']}, {'status':1, 'status_timestamp': now})
        log_event ('', 'create', detail={'group_id': g['id'], 'group_name': g['group_name'], 'members': kerbs}, forcelive=forcelive)
        print("created group %s with members %s" % (g['group_name'], kerbs))

    cnum = g['class_number']
    message = "Welcome to the <b>%s</b> pset group <b>%s</b>!" % (cnum, g['group_name'])
    db.messages.insert_many([{'type': 'newgroup', 'content': message, 'recipient_kerb': s['kerb'], 'sender_kerb':''} for s in students], resort=False)
    subject = new_group_subject.format(class_number=g['class_number'])
    url = student_url(g['class_number'], forcelive=forcelive)
    body = new_group_email.format(class_number=g['class_number'],url=url)
    send_email([email_address(s) for s in students], subject, body + signature, forcelive=forcelive)
    return g
 
def process_matches (matches, forcelive=False, match_run=-1):
    """
    Takes a dictionary returned by all_matches, keys are class_id's, values are objects with attributes
    groups = list of lists of kerbes, unmatched = list of tuples (kerb, reason) where reason is 'only' or 'requirement'
    only means there was only one member of the pool, requirement means a required preference could not be satisifed
    """
    db = getdb(forcelive)
    if match_run < 0:
        r = db.globals.lookup('match_run')
        match_run = r['value']+1 if r else 0
    db.globals.update({'key':'match_run'},{'timestamp': datetime.datetime.now(), 'value': match_run}, resort=False)

    n = 0
    for class_id in matches:
        for kerbs in matches[class_id]['groups']:
            g = create_group(class_id, kerbs, match_run=match_run, forcelive=forcelive)
            print("Created group %s (%s) in %s with %s members: %s" % (g['group_name'], g['id'], g['class_number'], len(kerbs), kerbs))
            n += 1
    print("Created %d new groups in match_run %d" % (n, match_run))
