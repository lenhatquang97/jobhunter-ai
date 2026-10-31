/** @odoo-module **/
import { Component, onMounted, onWillUnmount, proxy, t, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { routerBus } from "@web/core/browser/router";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { JobhunterChat } from "./chat";
import { panelWidth, recruitmentContext } from "./panel_utils";

registry.category("services").add("jobhunter_panel", {
    dependencies: ["action", "menu"],
    async start(env, {action, menu}) {
        const permitted = await user.hasGroup("hr_recruitment.group_hr_recruitment_interviewer");
        const state = proxy({active:false, open:true, width:420, currentRecord:null, mounted:false});
        const apply = () => {
            const context = recruitmentContext(action.currentController, menu.getCurrentApp());
            state.active = permitted && context.active;
            if (state.active) state.mounted = true;
            state.currentRecord = context.record;
            document.body.classList.toggle("jh-panel-visible", state.active && state.open);
            document.body.style.setProperty("--jh-panel-width", `${state.width}px`);
        };
        env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", apply);
        env.bus.addEventListener("MENUS:APP-CHANGED", apply);
        routerBus.addEventListener("ROUTE_CHANGE", apply);
        return {state, update:apply,
            toggle() { state.open=!state.open; apply(); },
            show() { state.open=true; apply(); },
            resize(value) { state.width=panelWidth(value); apply(); },
        };
    },
});

export class RecruitmentPanel extends Component {
    static template="t10_jobhunter_chat.RecruitmentPanel";
    static components={JobhunterChat};
    setup() {
        this.panel=useService("jobhunter_panel");
        this.state=this.panel.state;
        this.chatAction={tag:"t10_jobhunter_chat.panel"};
        onMounted(() => this.panel.update());
        onWillUnmount(() => this.stopResize());
    }
    startResize(ev) {
        ev.preventDefault();
        ev.currentTarget.setPointerCapture?.(ev.pointerId);
        this.move=(event)=>this.panel.resize(window.innerWidth-event.clientX);
        this.end=()=>this.stopResize();
        window.addEventListener("pointermove",this.move);
        window.addEventListener("pointerup",this.end,{once:true});
    }
    stopResize() {
        window.removeEventListener("pointermove",this.move);
        window.removeEventListener("pointerup",this.end);
    }
    resizeKey(ev) {
        const delta={ArrowLeft:20,ArrowRight:-20}[ev.key];
        if (delta) { ev.preventDefault(); this.panel.resize(this.state.width+delta); }
        if (ev.key === "Home") this.panel.resize(420);
    }
}

class PanelToggle extends Component {
    static template="t10_jobhunter_chat.PanelToggle";
    setup() { this.panel=useService("jobhunter_panel"); }
}

class OpenWorkspace extends Component {
    static template="t10_jobhunter_chat.OpenWorkspace";
    props=useProps({...standardActionServiceProps});
    setup() {
        const panel=useService("jobhunter_panel"), action=useService("action");
        onMounted(async () => {
            panel.show();
            await action.doAction({type:"ir.actions.act_window",name:"Applications",res_model:"hr.applicant",views:[[false,"kanban"],[false,"list"],[false,"form"]],target:"current"},{clearBreadcrumbs:true});
            panel.update();
        });
    }
}
registry.category("main_components").add("t10_jobhunter_chat.panel",{Component:RecruitmentPanel});
registry.category("systray").add("t10_jobhunter_chat.toggle",{Component:PanelToggle},{sequence:15});
registry.category("actions").add("t10_jobhunter_chat.workspace",OpenWorkspace);
