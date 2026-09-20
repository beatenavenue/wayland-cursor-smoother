// KWin Scripting (JS)
// Purpose: Notify an external daemon via DBus whenever cursor coordinates change

// Dependencies: workspace.cursorPos and cursorPosChanged signal
// Update frequency: Callback from KWin when coordinates change (no polling needed)

const SERVICE = "org.example.Glide";
const PATH    = "/glide";
const IFACE   = "org.example.Glide";

// Many KWin JS implementations have callDBus(service, path, interface, method, ...args)
function sendPos(p) {
    // Also pass timestamp (can be used for differential velocity calculation)
    callDBus(SERVICE, PATH, IFACE, "Update", p.x, p.y, Date.now());
}

function onMove(pos) {
    // pos is QPointF
    sendPos(pos);
}

print("Cursor Feed loaded");
workspace.cursorPosChanged.connect((pos) => {
    print("cursor:", pos.x, pos.y);
    onMove(pos);
});

// Initial update on startup
sendPos(workspace.cursorPos);
