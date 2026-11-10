/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const MODEL = "mst.receipt.monitor.line";
const PILLS = { received: "ok", pending: "warn", late_received: "ok", late: "bad" };
const NOTES = { late_received: "Jadwal sebelum hari ini", late: "Jadwal sebelum hari ini" };
const AGE_ORDER = ["today", "1_3", "4_7", "8_14", "15"];
const ROW_LIMIT = 200;
const GROUP_BY = [
    { key: "partner", label: "Partner" },
    { key: "age", label: "Umur" },
    { key: "group", label: "Kelompok" },
    { key: "dest", label: "Ke" },
];

export class ReceiptMonitor extends Component {
    static template = "mst17_receipt_monitor.ReceiptMonitor";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.groupByOptions = GROUP_BY;
        this.state = useState({
            date: false,
            // Cakupan: the kinds of Receipts counted ("2" = 2 langkah, "1" = 1 langkah). False = all.
            steps: false,
            scopeOpen: false,
            data: null,
            loading: false,
            // The search of the list under the summary, like the search bar of Odoo:
            // a Filter (Kelompok and Umur), a text, and a Group by.
            filter: { group: "late", age: false },
            search: "",
            groupBy: "partner",
            menuOpen: false,
            rows: [],
            // A long list shows the first rows only, until "Tampilkan semua" is clicked.
            showAll: false,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(MODEL, "get_monitor_data", [
                this.state.date || false,
                this.state.steps,
            ]);
            this.state.date = this.state.data.date;
            // The Cakupan the server used (all the kinds the first time).
            this.state.steps = this.state.data.scope_options.filter((o) => o.selected).map((o) => o.key);
            // All the WH/IN of the day come with the summary (one call); the Filter, the search and the
            // Group by work on them here.
            this.state.rows = this.state.data.rows;
            this.state.showAll = false;
        } finally {
            this.state.loading = false;
        }
    }

    onDateChange(ev) {
        this.state.date = ev.target.value;
        this.load();
    }

    // ---- Cakupan: choose the kinds of Receipts that are counted (at least one stays chosen)
    toggleScopeMenu() {
        this.state.scopeOpen = !this.state.scopeOpen;
    }

    toggleScope(key) {
        const steps = this.state.steps.includes(key)
            ? this.state.steps.filter((k) => k !== key)
            : [...this.state.steps, key];
        if (!steps.length) {
            return;
        }
        this.state.steps = steps;
        this.load();
    }

    get scopeLabel() {
        return this.state.data.scope_options
            .filter((o) => o.selected)
            .map((o) => o.label)
            .join(" + ");
    }

    // ---- Filter, search and group by
    select(group, age = false) {
        this.state.filter = { group, age };
        this.state.showAll = false;
    }

    isSelected(group, age = false) {
        return this.state.filter.group === group && this.state.filter.age === age;
    }

    clearGroupFilter() {
        this.state.filter = { group: false, age: false };
        this.state.showAll = false;
    }

    clearAgeFilter() {
        this.state.filter = { group: this.state.filter.group, age: false };
        this.state.showAll = false;
    }

    setAge(age) {
        // The age is of the late ones.
        this.state.filter = { group: "late", age: this.state.filter.age === age ? false : age };
        this.state.showAll = false;
    }

    setGroup(group) {
        this.state.filter = { group: this.state.filter.group === group ? false : group, age: false };
        this.state.showAll = false;
    }

    setGroupBy(key) {
        this.state.groupBy = this.state.groupBy === key ? false : key;
    }

    onSearch(ev) {
        this.state.search = ev.target.value;
        this.state.showAll = false;
    }

    showAll() {
        this.state.showAll = true;
    }

    toggleMenu() {
        this.state.menuOpen = !this.state.menuOpen;
    }

    closeMenu() {
        this.state.menuOpen = false;
    }

    get groupFilterLabel() {
        const group = this.state.data.groups.find((g) => g.key === this.state.filter.group);
        return group ? group.label : "";
    }

    get ageFilterLabel() {
        const age = this.state.data.ages.find((a) => a.key === this.state.filter.age);
        return age ? age.label : "";
    }

    get groupByLabel() {
        const option = GROUP_BY.find((o) => o.key === this.state.groupBy);
        return option ? option.label : "";
    }

    get visibleRows() {
        const { group, age } = this.state.filter;
        const text = this.state.search.trim().toLowerCase();
        return this.state.rows.filter((row) => {
            if (group && row.group !== group) {
                return false;
            }
            if (age && row.age !== age) {
                return false;
            }
            return !text || `${row.name} ${row.partner} ${row.origin}`.toLowerCase().includes(text);
        });
    }

    // The visible rows under a line per group: "Vendor A (3)". Without a Group by, one list.
    // More rows than ROW_LIMIT: only the first ones are drawn (the list stays fast), until "Tampilkan semua".
    get hiddenCount() {
        return this.state.showAll ? 0 : Math.max(this.visibleRows.length - ROW_LIMIT, 0);
    }

    get groupedRows() {
        const by = this.state.groupBy;
        const rows = this.state.showAll ? this.visibleRows : this.visibleRows.slice(0, ROW_LIMIT);
        if (!by) {
            return [{ label: false, rows }];
        }
        const keyOf = {
            partner: (r) => r.partner,
            age: (r) => r.age_label,
            group: (r) => r.group_label,
            dest: (r) => r.dest,
        }[by];
        const groups = new Map();
        for (const row of rows) {
            const key = keyOf(row);
            if (!groups.has(key)) {
                groups.set(key, { label: key, order: by === "age" ? AGE_ORDER.indexOf(row.age) : 0, rows: [] });
            }
            groups.get(key).rows.push(row);
        }
        const result = [...groups.values()];
        if (by === "age") {
            result.sort((a, b) => a.order - b.order);
        }
        return result;
    }

    // The full list (group by and Export) in its own screen, with the same Filter, search and Group by.
    async openList() {
        const { group, age } = this.state.filter;
        const action = await this.orm.call(MODEL, "action_open_group", [
            this.state.date,
            group,
            age,
            this.state.groupBy,
            this.state.search,
            this.state.steps,
        ]);
        this.action.doAction(action);
    }

    openPicking(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.picking",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    pill(key) {
        return PILLS[key] || "";
    }

    note(key) {
        return NOTES[key] || "";
    }

    // Numbers like 24.860; an empty value shows a dash.
    fmt(value) {
        return value ? Math.round(value).toLocaleString("id-ID") : "–";
    }
}

registry.category("actions").add("mst17_receipt_monitor.receipt_monitor", ReceiptMonitor);
