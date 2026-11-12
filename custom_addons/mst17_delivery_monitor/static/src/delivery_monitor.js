/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const MODEL = "mst.delivery.monitor.line";
const PERIODS = [["today", "Hari ini"], ["late", "Tertunda"], ["d1", "H+1"], ["d2", "H+2"], ["d3", "H+3"]];
const SCOPES = [["operational", "Operasional"], ["sample", "Sample"], ["all", "Semua"]];
const CATEGORIES = [["all", "Semua"], ["denim", "Denim & BBSF"], ["greige", "Greige"], ["non_denim", "Non-Denim"]];
const TITLES = { today: "hari ini", late: "tertunda", d1: "H+1", d2: "H+2", d3: "H+3" };
const STATUS_PILLS = { waiting: "warn", ready: "info", qty_real: "info", done: "ok" };
const ISSUE_PILLS = { available: "warn", none: "bad" };
const ISSUE_NOTES = { available: "Stok MST cukup", short: "Kurang / dalam produksi (WIP)" };

export class DeliveryMonitor extends Component {
    static template = "mst17_delivery_monitor.DeliveryMonitor";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.scopes = SCOPES;
        this.categories = CATEGORIES;
        this.state = useState({
            date: false,
            period: "today",
            scope: "operational",
            category: "all",
            data: null,
            loading: false,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            const { date, period, scope, category } = this.state;
            this.state.data = await this.orm.call(MODEL, "get_monitor_data", [date || false, period, scope, category]);
            this.state.date = this.state.data.date;
        } finally {
            this.state.loading = false;
        }
    }

    onDateChange(ev) {
        this.state.date = ev.target.value;
        this.load();
    }

    choose(field, value) {
        if (this.state[field] !== value) {
            this.state[field] = value;
            this.load();
        }
    }

    // A row of the summary opens its documents in an Odoo list (group by and Export).
    async open(panel, key, count) {
        if (!count) {
            return;
        }
        const { date, period, scope, category } = this.state;
        this.action.doAction(await this.orm.call(MODEL, "action_open_list", [date, period, scope, category, panel, key]));
    }

    get title() {
        return `Dokumen Kiriman ${TITLES[this.state.period]} · ${this.state.data.count}`;
    }

    get scopeLabel() {
        return SCOPES.find((s) => s[0] === this.state.scope)[1];
    }

    statusPill(key) {
        return STATUS_PILLS[key] || "";
    }

    issuePill(key) {
        return ISSUE_PILLS[key] || "";
    }

    issueNote(issue) {
        return issue.key === "qc" ? issue.note : ISSUE_NOTES[issue.key] || "";
    }

    // Numbers like 24.860; zero shows a dash.
    fmt(value) {
        return value ? Math.round(value).toLocaleString("id-ID") : "–";
    }
}

registry.category("actions").add("mst17_delivery_monitor.delivery_monitor", DeliveryMonitor);
