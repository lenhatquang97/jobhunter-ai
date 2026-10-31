/** @odoo-module **/
export function panelWidth(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(360, Math.min(640, number)) : 420;
}
export function recruitmentContext(controller, app) {
    const action = controller?.action || {};
    const model = action.res_model;
    const id = controller?.currentState?.resId || controller?.props?.resId || action.res_id;
    return {
        active: model === "hr.applicant" || model === "hr.job" || app?.xmlid === "hr_recruitment.menu_hr_recruitment_root" || action.tag === "t10_jobhunter_chat.workspace",
        record: ["hr.applicant", "hr.job"].includes(model) && Number.isInteger(id) && id > 0 ? {model, id, label: model === "hr.applicant" ? `Current applicant #${id}` : `Current job #${id}`} : null,
    };
}
