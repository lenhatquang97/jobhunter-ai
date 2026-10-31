/** @odoo-module **/

import { useListener } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

// Keep the sidebar selection in sync with navigation within Recruitment.
patch(NavBar.prototype, {
    setup() {
        super.setup();
        this.state.ahoLocation = window.location.pathname;
        try {
            this.state.ahoSidebarCollapsed = window.localStorage.getItem("aho.sidebar.collapsed") === "true";
        } catch {
            this.state.ahoSidebarCollapsed = false;
        }
        useListener(window, "popstate", () => {
            this.state.ahoLocation = window.location.pathname;
        });
    },

    async _onMenuClicked(menu) {
        await super._onMenuClicked(menu);
        this.state.ahoLocation = window.location.pathname;
    },

    toggleAhoSidebar() {
        this.state.ahoSidebarCollapsed = !this.state.ahoSidebarCollapsed;
        try {
            window.localStorage.setItem("aho.sidebar.collapsed", String(this.state.ahoSidebarCollapsed));
        } catch { /* Navigation remains usable when browser storage is unavailable. */ }
    },

    findAhoMenu(xmlid) {
        const find = (items) => {
            for (const item of items || []) {
                if (item.xmlid === xmlid) {
                    return item;
                }
                const child = find(item.childrenTree);
                if (child) {
                    return child;
                }
            }
            return null;
        };
        return find(this.currentAppSections);
    },

    isSidebarMenuActive(menu) {
        const path = this.getMenuItemHref(menu).split("?")[0];
        return this.state.ahoLocation === path || this.state.ahoLocation.startsWith(`${path}/`);
    },
});
