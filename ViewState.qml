pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
Singleton {
  id: root
  property string selected: "cpu"
  function select(value) {
    selected = value
    stateFile.setText(JSON.stringify({selected: value}) + "\n")
  }
  FileView {
    id: stateFile
    path: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/omarchy/maks.sysstats-graph.json"
    blockLoading: true
    atomicWrites: true
    printErrors: false
    onLoaded: { try { var data = JSON.parse(text()); if (data.selected) root.selected = data.selected } catch(e) {} }
  }
}
