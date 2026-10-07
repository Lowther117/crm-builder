"""Ready-made starting points for the setup wizard, each with example records."""
from __future__ import annotations

import datetime as _dt

from . import blueprint as bpm
from . import validate


def F(name, kind="text", **kw):
    """Field shorthand. opts=[...], link='type_key', req, lst (show in list), sec."""
    f = {"name": name, "kind": kind}
    m = {"req": "required", "lst": "in_list", "sec": "section", "opts": "options",
         "link": "link_type", "uniq": "unique"}
    for k, v in kw.items():
        f[m.get(k, k)] = v
    return f


def T(name, plural, prefix, fields, title, board=""):
    return {"name": name, "plural": plural, "prefix": prefix, "fields": fields,
            "title": title, "board": board}


ORG_FIELDS = [
    F("Name", req=True, lst=True),
    F("Type", "choice", opts=["Prospect", "Customer", "Partner", "Supplier", "Former customer"], lst=True),
    F("Phone", "phone", lst=True, sec="Contact details"),
    F("Email", "email", sec="Contact details"),
    F("Website", "url", sec="Contact details"),
    F("Address", "longtext", sec="Address"),
    F("Postcode", "postcode", lst=True, sec="Address"),
    F("Account owner", "user", default="me", lst=True, sec="Internal"),
]
CONTACT_FIELDS = [
    F("First name", req=True, lst=True),
    F("Last name", req=True, lst=True),
    F("Job title", lst=True),
    F("Organisation", "link", link="organisation", lst=True),
    F("Email", "email", lst=True, sec="Contact details"),
    F("Mobile", "phone", lst=True, sec="Contact details"),
    F("Other phone", "phone", sec="Contact details"),
    F("Prefers", "choice", opts=["Email", "Phone", "Text", "Post"], sec="Contact details"),
    F("Happy to receive marketing", "yesno", sec="Preferences",
      help="Only tick when they have told you so."),
    F("Tags", "tags", opts=["VIP", "Decision maker", "Newsletter", "Do not contact"], sec="Preferences"),
]

TEMPLATES = [
    {
        "key": "sales", "name": "Customers and sales",
        "blurb": "Organisations, the people in them, and a pipeline of deals you are trying to win.",
        "types": [
            T("Organisation", "Organisations", "ORG", ORG_FIELDS, ["name"]),
            T("Contact", "Contacts", "CON", CONTACT_FIELDS, ["first_name", "last_name"]),
            T("Deal", "Deals", "DEAL", [
                F("Name", req=True, lst=True, help="What is being sold, e.g. 'Website redesign'."),
                F("Organisation", "link", link="organisation", lst=True),
                F("Main contact", "link", link="contact"),
                F("Stage", "choice", req=True, lst=True, default="New lead",
                  opts=["New lead", "Qualified", "Proposal sent", "Negotiating", "Won", "Lost"]),
                F("Value", "money", lst=True, min=0, sec="The numbers"),
                F("Chance of winning", "percent", min=0, max=100, sec="The numbers"),
                F("Expected to close", "date", lst=True, remind=True, sec="The numbers"),
                F("Source", "choice", opts=["Referral", "Website", "Event", "Cold outreach",
                                            "Existing customer", "Other"], sec="Background"),
                F("Owner", "user", default="me", lst=True, sec="Background"),
                F("Why lost", sec="Background"),
            ], ["name"], board="stage"),
        ],
    },
    {
        "key": "contacts", "name": "Contacts and organisations",
        "blurb": "A shared address book: who people are, where they work, how to reach them.",
        "types": [
            T("Organisation", "Organisations", "ORG", ORG_FIELDS, ["name"]),
            T("Contact", "Contacts", "CON", CONTACT_FIELDS, ["first_name", "last_name"]),
        ],
    },
    {
        "key": "casework", "name": "Casework and clients",
        "blurb": "One case per person you support: their details, status, caseworker and next review.",
        "types": [
            T("Case", "Cases", "CASE", [
                F("First name", req=True, lst=True),
                F("Last name", req=True, lst=True),
                F("Date of birth", "date", date_rule="past"),
                F("National Insurance number", "ni", uniq=True),
                F("Mobile", "phone", lst=True, sec="Contact details"),
                F("Email", "email", sec="Contact details"),
                F("Address", "longtext", sec="Contact details"),
                F("Postcode", "postcode", sec="Contact details"),
                F("Status", "choice", req=True, lst=True, default="Referred", sec="The case",
                  opts=["Referred", "Assessment", "Active", "On hold", "Closed"]),
                F("Caseworker", "user", default="me", lst=True, sec="The case"),
                F("Referred by", "link", link="referrer", sec="The case"),
                F("Referral date", "date", default="today", date_rule="past", sec="The case"),
                F("Next review", "date", lst=True, remind=True, sec="The case"),
                F("Support needs", "tags", sec="The case",
                  opts=["Employment", "Housing", "Money and debt", "Health", "Training", "Benefits"]),
                F("Consent to store information", "yesno", sec="Consent",
                  help="Tick once the person has agreed to you keeping their details."),
                F("Consent date", "date", date_rule="past", sec="Consent"),
            ], ["first_name", "last_name"], board="status"),
            T("Referrer", "Referrers", "REF", [
                F("Name", req=True, lst=True),
                F("Type", "choice", lst=True, opts=["GP surgery", "Jobcentre", "Council", "Charity",
                                                     "Employer", "Self-referral", "Other"]),
                F("Contact name", lst=True),
                F("Phone", "phone", lst=True),
                F("Email", "email"),
            ], ["name"]),
        ],
    },
    {
        "key": "tenders", "name": "Tenders and bids",
        "blurb": "Opportunities you have spotted, who the buyer is, the deadline and how each bid went.",
        "types": [
            T("Buyer", "Buyers", "BUY", [
                F("Name", req=True, lst=True),
                F("Sector", "choice", lst=True, opts=["Central government", "Local government",
                                                       "Health", "Education", "Housing", "Private", "Other"]),
                F("Procurement contact"),
                F("Email", "email", lst=True),
                F("Phone", "phone"),
                F("Website", "url"),
            ], ["name"]),
            T("Tender", "Tenders", "TEN", [
                F("Title", req=True, lst=True),
                F("Buyer", "link", link="buyer", lst=True),
                F("Reference", help="The buyer's own reference number."),
                F("Stage", "choice", req=True, lst=True, default="Spotted",
                  opts=["Spotted", "Reviewing", "Bidding", "Submitted", "Won", "Lost", "No bid"]),
                F("Contract value", "money", lst=True, min=0, sec="Key facts"),
                F("Deadline", "date", lst=True, remind=True, sec="Key facts"),
                F("Decision expected", "date", remind=True, sec="Key facts"),
                F("Where it was advertised", "url", sec="Key facts"),
                F("Bid lead", "user", default="me", lst=True, sec="Our bid"),
                F("Our price", "money", min=0, sec="Our bid"),
                F("Feedback", "longtext", sec="Our bid"),
            ], ["title"], board="stage"),
        ],
    },
    {
        "key": "members", "name": "Members and volunteers",
        "blurb": "For clubs, charities and community groups: members, renewals and payments received.",
        "types": [
            T("Member", "Members", "MEM", [
                F("First name", req=True, lst=True),
                F("Last name", req=True, lst=True),
                F("Email", "email", lst=True, sec="Contact details"),
                F("Mobile", "phone", lst=True, sec="Contact details"),
                F("Address", "longtext", sec="Contact details"),
                F("Postcode", "postcode", sec="Contact details"),
                F("Membership", "choice", lst=True, sec="Membership",
                  opts=["Full", "Concession", "Family", "Life", "Volunteer only"]),
                F("Status", "choice", req=True, lst=True, default="Active", sec="Membership",
                  opts=["Applied", "Active", "Lapsed", "Left"]),
                F("Joined", "date", date_rule="past", sec="Membership"),
                F("Renewal due", "date", lst=True, remind=True, sec="Membership"),
                F("Gift Aid declaration held", "yesno", sec="Membership"),
                F("Can help with", "tags", sec="Volunteering",
                  opts=["Events", "Fundraising", "Driving", "Admin", "Committee", "First aid"]),
            ], ["first_name", "last_name"]),
            T("Payment", "Payments", "PAY", [
                F("Member", "link", link="member", req=True, lst=True),
                F("Amount", "money", req=True, lst=True, min=0),
                F("Date received", "date", req=True, lst=True, default="today", date_rule="past"),
                F("For", "choice", lst=True, opts=["Subscription", "Donation", "Event", "Other"]),
                F("Paid by", "choice", lst=True, opts=["Bank transfer", "Card", "Cash", "Cheque",
                                                        "Standing order"]),
            ], ["member", "date_received"]),
        ],
    },
    {
        "key": "property", "name": "Properties and tenants",
        "blurb": "For landlords and lettings: properties, who lives in them, and repair jobs.",
        "types": [
            T("Property", "Properties", "PROP", [
                F("Address", req=True, lst=True),
                F("Postcode", "postcode", lst=True),
                F("Type", "choice", lst=True, opts=["House", "Flat", "HMO room", "Commercial", "Garage"]),
                F("Bedrooms", "number", decimals=0, min=0),
                F("Status", "choice", req=True, lst=True, default="Vacant",
                  opts=["Vacant", "Under offer", "Let", "Being refurbished"]),
                F("Monthly rent", "money", lst=True, min=0, sec="Money"),
                F("Gas safety check due", "date", remind=True, sec="Compliance"),
                F("Electrical check due", "date", remind=True, sec="Compliance"),
                F("EPC rating", "choice", opts=list("ABCDEFG"), sec="Compliance"),
            ], ["address"], board="status"),
            T("Tenant", "Tenants", "TNT", [
                F("First name", req=True, lst=True),
                F("Last name", req=True, lst=True),
                F("Property", "link", link="property", lst=True),
                F("Mobile", "phone", lst=True),
                F("Email", "email", lst=True),
                F("Tenancy started", "date", sec="Tenancy"),
                F("Tenancy ends", "date", lst=True, remind=True, sec="Tenancy"),
                F("Deposit held", "money", min=0, sec="Tenancy"),
            ], ["first_name", "last_name"]),
            T("Repair", "Repairs", "JOB", [
                F("Summary", req=True, lst=True),
                F("Property", "link", link="property", req=True, lst=True),
                F("Status", "choice", req=True, lst=True, default="Reported",
                  opts=["Reported", "Quoted", "Booked", "Done", "Cancelled"]),
                F("Reported on", "date", default="today", lst=True, date_rule="past"),
                F("Contractor"),
                F("Cost", "money", lst=True, min=0),
            ], ["summary"], board="status"),
        ],
    },
    {
        "key": "suppliers", "name": "Suppliers and contracts",
        "blurb": "Who you buy from, what you have signed, what it costs and when it ends.",
        "types": [
            T("Supplier", "Suppliers", "SUP", [
                F("Name", req=True, lst=True),
                F("Category", "choice", lst=True, opts=["IT and software", "Premises", "Insurance",
                                                         "Professional services", "Utilities",
                                                         "Training", "Other"]),
                F("Contact name", lst=True),
                F("Email", "email", lst=True),
                F("Phone", "phone"),
                F("Website", "url"),
                F("Approved supplier", "yesno", lst=True),
            ], ["name"]),
            T("Contract", "Contracts", "CTR", [
                F("Title", req=True, lst=True),
                F("Supplier", "link", link="supplier", req=True, lst=True),
                F("Status", "choice", req=True, lst=True, default="Live",
                  opts=["Being agreed", "Live", "In notice", "Ended"]),
                F("Yearly cost", "money", lst=True, min=0, sec="Terms"),
                F("Starts", "date", sec="Terms"),
                F("Ends", "date", lst=True, remind=True, sec="Terms"),
                F("Renewal", "choice", opts=["Renews automatically", "Fixed term", "Rolling monthly"],
                  sec="Terms"),
                F("Notice needed", sec="Terms", help="e.g. '90 days before the end date'."),
                F("Give notice by", "date", remind=True, sec="Terms"),
                F("Owner", "user", default="me", lst=True, sec="Internal"),
            ], ["title"], board="status"),
        ],
    },
    {
        "key": "jobs", "name": "Job applications",
        "blurb": "A personal tracker: employers, the roles you have applied for and where each one stands.",
        "types": [
            T("Employer", "Employers", "EMP", [
                F("Name", req=True, lst=True),
                F("Sector", lst=True),
                F("Website", "url", lst=True),
                F("Location", lst=True),
            ], ["name"]),
            T("Application", "Applications", "APP", [
                F("Role", req=True, lst=True),
                F("Employer", "link", link="employer", lst=True),
                F("Stage", "choice", req=True, lst=True, default="Interested",
                  opts=["Interested", "Applied", "Interview", "Offer", "Rejected", "Withdrawn"]),
                F("Closing date", "date", lst=True, remind=True),
                F("Applied on", "date", date_rule="past"),
                F("Interview date", "date", remind=True),
                F("Salary", lst=True, help="As advertised, e.g. '£32,000 - £36,000'."),
                F("Advert", "url"),
                F("Contact name", sec="Contact"),
                F("Contact email", "email", sec="Contact"),
            ], ["role", "employer"], board="stage"),
        ],
    },
]


def template(key: str) -> dict | None:
    for t in TEMPLATES:
        if t["key"] == key:
            return t
    return None


def build(key: str, name: str = "My CRM") -> dict:
    """Turn a template into a full blueprint."""
    bp = bpm.empty(name)
    tpl = template(key)
    if tpl is None:
        return bp
    for i, t in enumerate(tpl["types"]):
        nt = {"key": bpm.slug(t["name"]), "name": t["name"], "plural": t["plural"],
              "prefix": t["prefix"], "color": bpm.COLORS[i % len(bpm.COLORS)],
              "fields": [], "title": list(t["title"]), "board": t.get("board", "")}
        keys: list[str] = []
        for f in t["fields"]:
            nf = dict(f)
            nf["key"] = bpm.slug(f["name"], keys)
            keys.append(nf["key"])
            nt["fields"].append(nf)
        bp["types"].append(nt)
    return bpm.clean(bp)


def _d(days: int) -> str:
    return (_dt.date.today() + _dt.timedelta(days=days)).isoformat()


# Example records: (type key, values). "@type:n" points at the n-th example of
# that type; "@me" is whoever is signed in.
SAMPLES = {
    "sales": [
        ("organisation", {"name": "Brecon Outdoor Supplies", "type": "Customer", "phone": "01874 555010",
                          "email": "hello@breconoutdoor.example", "postcode": "LD3 7AA",
                          "account_owner": "@me"}),
        ("organisation", {"name": "Taff Valley Print", "type": "Prospect", "phone": "01685 555020",
                          "website": "https://taffvalleyprint.example", "postcode": "CF47 8DP",
                          "account_owner": "@me"}),
        ("contact", {"first_name": "Megan", "last_name": "Price", "job_title": "Managing Director",
                     "organisation": "@organisation:0", "email": "megan@breconoutdoor.example",
                     "mobile": "07700 900111", "prefers": "Email", "tags": ["Decision maker"]}),
        ("contact", {"first_name": "Owen", "last_name": "Hughes", "job_title": "Operations Lead",
                     "organisation": "@organisation:1", "email": "owen@taffvalleyprint.example",
                     "mobile": "07700 900222", "prefers": "Phone"}),
        ("deal", {"name": "Online shop rebuild", "organisation": "@organisation:0",
                  "main_contact": "@contact:0", "stage": "Proposal sent", "value": 8500,
                  "chance_of_winning": 60, "expected_to_close": _d(12), "source": "Existing customer",
                  "owner": "@me"}),
        ("deal", {"name": "Brochure and signage", "organisation": "@organisation:1",
                  "main_contact": "@contact:1", "stage": "New lead", "value": 2400,
                  "chance_of_winning": 20, "expected_to_close": _d(40), "source": "Referral",
                  "owner": "@me"}),
        ("deal", {"name": "Annual support plan", "organisation": "@organisation:0",
                  "stage": "Won", "value": 3600, "chance_of_winning": 100,
                  "expected_to_close": _d(-9), "source": "Existing customer", "owner": "@me"}),
    ],
    "contacts": [
        ("organisation", {"name": "Brecon Outdoor Supplies", "type": "Customer", "phone": "01874 555010",
                          "postcode": "LD3 7AA", "account_owner": "@me"}),
        ("organisation", {"name": "Taff Valley Print", "type": "Supplier", "phone": "01685 555020",
                          "postcode": "CF47 8DP", "account_owner": "@me"}),
        ("contact", {"first_name": "Megan", "last_name": "Price", "job_title": "Managing Director",
                     "organisation": "@organisation:0", "email": "megan@breconoutdoor.example",
                     "mobile": "07700 900111", "prefers": "Email"}),
        ("contact", {"first_name": "Owen", "last_name": "Hughes", "job_title": "Operations Lead",
                     "organisation": "@organisation:1", "email": "owen@taffvalleyprint.example",
                     "mobile": "07700 900222", "prefers": "Phone"}),
    ],
    "casework": [
        ("referrer", {"name": "Riverside Medical Practice", "type": "GP surgery",
                      "contact_name": "Dr A. Rees", "phone": "01443 555030"}),
        ("referrer", {"name": "Valleys Jobcentre", "type": "Jobcentre", "contact_name": "Work coach team"}),
        ("case", {"first_name": "Example", "last_name": "Person", "date_of_birth": "1988-04-17",
                  "mobile": "07700 900333", "postcode": "CF45 4AA", "status": "Active",
                  "caseworker": "@me", "referred_by": "@referrer:0", "referral_date": _d(-30),
                  "next_review": _d(5), "support_needs": ["Employment", "Health"],
                  "consent_to_store_information": True, "consent_date": _d(-30)}),
        ("case", {"first_name": "Sample", "last_name": "Client", "date_of_birth": "1995-11-02",
                  "mobile": "07700 900444", "status": "Referred", "caseworker": "@me",
                  "referred_by": "@referrer:1", "referral_date": _d(-2),
                  "support_needs": ["Training"]}),
    ],
    "tenders": [
        ("buyer", {"name": "Example County Council", "sector": "Local government",
                   "procurement_contact": "Procurement team",
                   "email": "procurement@examplecouncil.example"}),
        ("buyer", {"name": "Example Health Board", "sector": "Health"}),
        ("tender", {"title": "Employee wellbeing support service", "buyer": "@buyer:0",
                    "reference": "ECC-2291", "stage": "Bidding", "contract_value": 240000,
                    "deadline": _d(9), "decision_expected": _d(45), "bid_lead": "@me"}),
        ("tender", {"title": "Occupational health framework - Lot 3", "buyer": "@buyer:1",
                    "stage": "Spotted", "contract_value": 600000, "deadline": _d(31),
                    "bid_lead": "@me"}),
        ("tender", {"title": "Staff counselling pilot", "buyer": "@buyer:0", "stage": "Submitted",
                    "contract_value": 45000, "deadline": _d(-6), "decision_expected": _d(20),
                    "bid_lead": "@me", "our_price": 43800}),
    ],
    "members": [
        ("member", {"first_name": "Example", "last_name": "Member", "email": "member@example.org",
                    "mobile": "07700 900555", "postcode": "CF45 3PG", "membership": "Full",
                    "status": "Active", "joined": _d(-400), "renewal_due": _d(10),
                    "gift_aid_declaration_held": True, "can_help_with": ["Events", "Driving"]}),
        ("member", {"first_name": "Sample", "last_name": "Volunteer", "mobile": "07700 900666",
                    "membership": "Volunteer only", "status": "Active", "joined": _d(-90),
                    "can_help_with": ["Admin", "First aid"]}),
        ("payment", {"member": "@member:0", "amount": 30, "date_received": _d(-355),
                     "for": "Subscription", "paid_by": "Bank transfer"}),
    ],
    "property": [
        ("property", {"address": "14 Example Terrace, Mountain Ash", "postcode": "CF45 3AA",
                      "type": "House", "bedrooms": 3, "status": "Let", "monthly_rent": 725,
                      "gas_safety_check_due": _d(21), "epc_rating": "C"}),
        ("property", {"address": "Flat 2, 8 Sample Street, Aberdare", "postcode": "CF44 7AA",
                      "type": "Flat", "bedrooms": 1, "status": "Vacant", "monthly_rent": 495,
                      "epc_rating": "D"}),
        ("tenant", {"first_name": "Example", "last_name": "Tenant", "property": "@property:0",
                    "mobile": "07700 900777", "tenancy_started": _d(-200), "tenancy_ends": _d(165),
                    "deposit_held": 725}),
        ("repair", {"summary": "Boiler losing pressure", "property": "@property:0",
                    "status": "Booked", "reported_on": _d(-3), "contractor": "Valley Heating",
                    "cost": 120}),
    ],
    "suppliers": [
        ("supplier", {"name": "Example Telecom", "category": "IT and software",
                      "contact_name": "Account team", "phone": "0330 555 0100",
                      "approved_supplier": True}),
        ("supplier", {"name": "Sample Insurance Brokers", "category": "Insurance",
                      "approved_supplier": True}),
        ("contract", {"title": "Broadband and phones", "supplier": "@supplier:0", "status": "Live",
                      "yearly_cost": 1440, "starts": _d(-300), "ends": _d(65),
                      "renewal": "Renews automatically", "notice_needed": "30 days before the end date",
                      "give_notice_by": _d(35), "owner": "@me"}),
        ("contract", {"title": "Public liability cover", "supplier": "@supplier:1", "status": "Live",
                      "yearly_cost": 980, "starts": _d(-340), "ends": _d(25), "renewal": "Fixed term",
                      "owner": "@me"}),
    ],
    "jobs": [
        ("employer", {"name": "Example Housing Association", "sector": "Housing", "location": "Cardiff"}),
        ("employer", {"name": "Sample Software Ltd", "sector": "Technology", "location": "Remote"}),
        ("application", {"role": "Governance Officer", "employer": "@employer:0", "stage": "Applied",
                         "closing_date": _d(-4), "applied_on": _d(-6), "salary": "£34,000 - £38,000"}),
        ("application", {"role": "Compliance Analyst", "employer": "@employer:1",
                         "stage": "Interested", "closing_date": _d(8), "salary": "£40,000"}),
    ],
}
SAMPLE_TASKS = {
    "sales": [("deal", 0, "Chase Megan about the proposal", 2), ("deal", 1, "Send brochure samples", 5)],
    "contacts": [("contact", 0, "Catch-up call", 3)],
    "casework": [("case", 0, "Book review meeting", 3), ("case", 1, "First assessment call", 1)],
    "tenders": [("tender", 0, "Draft method statement", 4), ("tender", 1, "Decide: bid or no bid", 7)],
    "members": [("member", 0, "Send renewal reminder", 3)],
    "property": [("repair", 0, "Confirm engineer visit with tenant", 1)],
    "suppliers": [("contract", 1, "Get three renewal quotes", 6)],
    "jobs": [("application", 1, "Tailor CV and apply", 4)],
}


def load_samples(db, key: str) -> int:
    """Add the example records for a template. They are flagged as examples so
    they can all be removed again in one go. Returns how many were added.

    The design may no longer be the template's (lists and fields can be
    dropped or changed in the wizard): a value that does not fit the design as
    it is now is left out, and so is a record with nothing left in it."""
    made: dict[str, list] = {}
    count = 0
    with db.tx():
        for type_key, values in SAMPLES.get(key, []):
            t = bpm.get_type(db.blueprint, type_key)
            if t is None or t.get("archived"):
                continue
            data = {}
            for k, v in values.items():
                f = bpm.get_field(t, k)
                if f is None or f.get("archived"):
                    continue
                if v == "@me":
                    v = db.user["id"] if f["kind"] == "user" else None
                elif isinstance(v, str) and v.startswith("@"):
                    tk, _, idx = v[1:].partition(":")
                    ids = made.get(tk, [])
                    v = ids[int(idx)] if f["kind"] == "link" and f.get("link_type") == tk \
                        and int(idx) < len(ids) else None
                elif f["kind"] in ("link", "user"):
                    v = None
                else:
                    v, err = validate.normalise(dict(f, required=False),
                                                validate.to_edit(f, v) if f["kind"] != "tags" else v)
                    if err:
                        v = None
                if v is not None:
                    data[k] = v
            for f in bpm.active_fields(t) if data else []:
                if f.get("required") and f["key"] not in data:
                    d = bpm.default_value(f, db.user["id"])
                    if d is not None:
                        data[f["key"]] = d
            rid = db.create_record(type_key, data, example=True) if data else None
            made.setdefault(type_key, []).append(rid)       # (keeps "@type:n" counting right)
            count += 1 if rid else 0
        for type_key, idx, title, days in SAMPLE_TASKS.get(key, []):
            ids = made.get(type_key, [])
            if idx < len(ids) and ids[idx]:
                db.add_task(title, due=_d(days), record_id=ids[idx], assigned_to=db.user["id"])
    return count
