import csv

from odoo.exceptions import UserError
from odoo.tools import file_path

# Province codes of the Kemendagri dataset -> state names of Odoo's Indonesia (base.id)
PROVINCE_STATES = {
    "11": "Aceh", "12": "Sumatra Utara", "13": "Sumatra Barat", "14": "Riau", "15": "Jambi",
    "16": "Sumatra Selatan", "17": "Bengkulu", "18": "Lampung", "19": "Bangka Belitung",
    "21": "Kepulauan Riau", "31": "Jakarta", "32": "Jawa Barat", "33": "Jawa Tengah",
    "34": "Yogyakarta", "35": "Jawa Timur", "36": "Banten", "51": "Bali",
    "52": "Nusa Tenggara Barat", "53": "Nusa Tenggara Timur", "61": "Kalimantan Barat",
    "62": "Kalimantan Tengah", "63": "Kalimantan Selatan", "64": "Kalimantan Timur",
    "65": "Kalimantan Utara", "71": "Sulawesi Utara", "72": "Sulawesi Tengah",
    "73": "Sulawesi Selatan", "74": "Sulawesi Tenggara", "75": "Gorontalo",
    "76": "Sulawesi Barat", "81": "Maluku", "82": "Maluku Utara", "91": "Papua",
    "92": "Papua Barat", "93": "Papua Selatan", "94": "Papua Tengah",
    "95": "Papua Pegunungan", "96": "Papua Barat Daya",
}


def post_init_hook(env):
    indonesia = env.ref("base.id")
    states = {state.name: state.id for state in indonesia.state_ids}
    missing = sorted(set(PROVINCE_STATES.values()) - states.keys())
    if missing:
        raise UserError("These Indonesian states are missing in Odoo: %s" % ", ".join(missing))

    with open(file_path("mst17_address_id/data/res_city.csv"), encoding="utf-8", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    env["res.city"].create([{
        "name": row["name"],
        "country_id": indonesia.id,
        "state_id": states[PROVINCE_STATES[row["state_code"]]],
    } for row in rows])
    indonesia.enforce_cities = True
