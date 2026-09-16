// Toolbar chrome for the VPforce TelemFFB settings panel.
//
// This file only owns the panel window (show/hide/resize via ingame-ui).
// The actual settings UI lives in panel.html, loaded into the iframe below
// and talking to telemffb/api_server.py over HTTP - see panel.js.
class IngamePanelVpforceSettings extends TemplateElement {
    constructor() {
        super(...arguments);

        this.panelActive = false;
        this.started = false;
        this.ingameUi = null;

        this.initialize();
    }
    connectedCallback() {
        super.connectedCallback();

        var self = this;
        this.ingameUi = this.querySelector('ingame-ui');

        this.iframeElement = document.getElementById("VpforceSettingsIframe");

        this.m_MainDisplay = document.querySelector("#MainDisplay");
        this.m_MainDisplay.classList.add("hidden");

        this.m_Footer = document.querySelector("#Footer");
        this.m_Footer.classList.add("hidden");

        if (this.ingameUi) {
            this.ingameUi.addEventListener("panelActive", (e) => {
                self.panelActive = true;
                if (self.iframeElement) {
                    // Path is absolute from the package's html_ui root, same
                    // convention as the /JS, /SCSS, /templates references above.
                    self.iframeElement.src = '/InGamePanels/VpforceSettings/panel.html';
                }
            });
            this.ingameUi.addEventListener("panelInactive", (e) => {
                self.panelActive = false;
                if (self.iframeElement) {
                    self.iframeElement.src = '';
                }
            });
        }
    }
    initialize() {
        if (this.started) {
            return;
        }
        this.started = true;
    }
    disconnectedCallback() {
        super.disconnectedCallback();
    }
}
window.customElements.define("ingamepanel-vpforce-settings", IngamePanelVpforceSettings);
checkAutoload();
