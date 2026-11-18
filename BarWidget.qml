import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "maks.sysstats-graph"
  property var snapshot: ({})
  property bool popupOpen: false
  property double now: Date.now()
  readonly property var metrics: [
    {id:"cpu", title:"Процессор", icon:String.fromCodePoint(0xf4bc)},
    {id:"ram", title:"Оперативная память", icon:String.fromCodePoint(0xe266)},
    {id:"gpu", title:"Видеокарта", icon:String.fromCodePoint(0xf08ae)},
    {id:"vram", title:"Видеопамять", icon:String.fromCodePoint(0xe266)},
    {id:"cpu_temp", title:"Температура CPU", icon:String.fromCodePoint(0xf050f)},
    {id:"gpu_temp", title:"Температура GPU", icon:String.fromCodePoint(0xf050f)},
    {id:"disk", title:"Диск", icon:String.fromCodePoint(0xf02ca)},
    {id:"net", title:"Сеть", icon:String.fromCodePoint(0xf0920)}
  ]
  readonly property var available: metrics.filter(function(m) { return root.snapshot.capabilities && root.snapshot.capabilities[m.id] })
  readonly property var metric: {
    for (var i=0; i<metrics.length; i++) if (metrics[i].id === ViewState.selected) return metrics[i]
    return metrics[0]
  }
  readonly property var latest: snapshot.latest || ({})
  readonly property var details: snapshot.details || ({})
  readonly property bool fresh: Boolean(snapshot.updatedAt) && now - snapshot.updatedAt < 15000
  readonly property bool hovered: button.tooltipHovered || popup.containsMouse
  readonly property color fg: bar ? bar.barForeground : Commons.Color.foreground
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  function valid(v) { return v !== null && v !== undefined && isFinite(Number(v)) }
  function percent(v) { return valid(v) && fresh ? Math.round(v) + "%" : "—" }
  function temp(v) { return valid(v) && fresh ? Math.round(v) + "°C" : "—" }
  function bytes(v) {
    if (!valid(v)) return "—"
    var units=["Б", "КБ", "МБ", "ГБ", "ТБ"], n=Number(v), i=0
    while (n>=1024 && i<units.length-1) {n/=1024; i++}
    return (i===0 ? n.toFixed(0) : n.toFixed(n<10 ? 1 : 0)) + " " + units[i]
  }
  function rate(v) { return bytes(v) + "/с" }
  function value(id) {
    if (id === "net") return fresh ? "↓ " + rate(latest.net_rx) + "   ↑ " + rate(latest.net_tx) : "—"
    if (id === "cpu_temp" || id === "gpu_temp") return temp(latest[id])
    return percent(latest[id])
  }
  function secondary(id) {
    if (id === "cpu" && valid(latest.cpu_temp)) return temp(latest.cpu_temp)
    if (id === "gpu" && valid(latest.gpu_temp)) return temp(latest.gpu_temp)
    if (id === "ram" && valid(details.ramUsed)) return bytes(details.ramUsed) + " / " + bytes(details.ramTotal)
    if (id === "vram" && valid(details.vramUsed)) return bytes(details.vramUsed) + " / " + bytes(details.vramTotal)
    if (id === "disk" && valid(details.diskUsed)) return bytes(details.diskUsed) + " / " + bytes(details.diskTotal)
    return ""
  }
  function cycle() {
    if (!available.length) return
    var index = -1
    for (var i=0; i<available.length; i++) if (available[i].id === metric.id) index=i
    ViewState.select(available[(index+1)%available.length].id)
  }
  function close() { popupOpen=false }
  onHoveredChanged: {
    if (hovered) { hideTimer.stop(); if (!popupOpen) showTimer.restart() }
    else { showTimer.stop(); hideTimer.restart() }
  }
  Timer { id: showTimer; interval: 130; onTriggered: root.popupOpen=true }
  Timer { id: hideTimer; interval: 220; onTriggered: if (!root.hovered) root.popupOpen=false }
  Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now=Date.now() }
  FileView {
    id: dataFile
    path: Quickshell.env("XDG_RUNTIME_DIR") + "/maks-sysstats-graph.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { try { root.snapshot=JSON.parse(text()); root.now=Date.now() } catch(e) {} }
  }
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    fixedWidth: root.vertical ? -1 : row.implicitWidth + 15
    fixedHeight: root.vertical ? row.implicitHeight + 12 : -1
    tooltipText: ""
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.LeftButton) root.cycle()
      else if (mouseButton === Qt.RightButton && root.bar) root.bar.run("omarchy-launch-or-focus-tui btop")
    }
    Row {
      id: row
      anchors.centerIn: parent
      spacing: 6
      Text { text: root.metric.icon; color:root.fg; font.family:root.bar ? root.bar.fontFamily : Style.font.family; font.pixelSize:Style.font.body+3; anchors.verticalCenter:parent.verticalCenter; renderType:Text.NativeRendering }
      Text { text:root.value(root.metric.id) + ((root.metric.id === "cpu" || root.metric.id === "gpu") && root.secondary(root.metric.id) ? " | " + root.secondary(root.metric.id) : ""); color:root.fg; font.family:root.bar ? root.bar.fontFamily : Style.font.family; font.pixelSize:Style.font.body+2; anchors.verticalCenter:parent.verticalCenter; renderType:Text.NativeRendering }
    }
  }
  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    triggerMode: "hover"
    open: root.popupOpen
    contentWidth: fittedContentWidth(330)
    contentHeight: fittedContentHeight(content.implicitHeight)
    Column {
      id: content
      width: parent.width
      spacing: 8
      Row {
        width: parent.width
        spacing: 8
        Text { text:root.metric.icon; color:Commons.Color.accent; font.family:root.bar ? root.bar.fontFamily : Style.font.family; font.pixelSize:20; anchors.verticalCenter:parent.verticalCenter }
        Text { text:root.metric.title; color:root.bar ? root.bar.foreground : Commons.Color.foreground; font.family:Style.font.family; font.pixelSize:15; font.bold:true; anchors.verticalCenter:parent.verticalCenter }
      }
      Row {
        width: parent.width
        spacing: 10
        Text { text:root.value(root.metric.id); color:root.bar ? root.bar.foreground : Commons.Color.foreground; font.family:Style.font.family; font.pixelSize:20; font.bold:true; anchors.verticalCenter:parent.verticalCenter }
        Text { text:root.secondary(root.metric.id); color:root.bar ? root.bar.foreground : Commons.Color.foreground; opacity:0.65; font.family:Style.font.family; font.pixelSize:11; anchors.verticalCenter:parent.verticalCenter }
      }
      Canvas {
        id: chart
        width: parent.width
        height: 100
        property var snapshotData: root.snapshot
        property string selected: root.metric.id
        onSnapshotDataChanged: if (root.popupOpen) requestPaint()
        onSelectedChanged: requestPaint()
        Connections { target: root; function onPopupOpenChanged() { if (root.popupOpen) chart.requestPaint() } }
        onPaint: {
          var ctx=getContext("2d"); ctx.clearRect(0,0,width,height)
          var history=(snapshotData && snapshotData.history) || {}, keys=selected === "net" ? ["net_rx","net_tx"] : [selected]
          var series=keys.map(function(k){return history[k] || []})
          var end=Date.now(), oldest=end, maxValue=0
          for (var s=0;s<series.length;s++) for(var i=0;i<series[s].length;i++) {
            var p=series[s][i]; if(p && p.length>=2 && p[1]!==null && isFinite(p[1])) {oldest=Math.min(oldest,p[0]); maxValue=Math.max(maxValue,p[1])}
          }
          var span=60000, start=end-span
          var maxY=selected === "net" ? Math.max(1,maxValue*1.2) : (selected.indexOf("temp")>=0 ? Math.max(100,Math.ceil(maxValue/10)*10) : 100)
          ctx.lineWidth=1; ctx.strokeStyle=Qt.rgba(0.5,0.5,0.5,0.22)
          for(var g=0;g<=4;g++){var y=1+(height-2)*g/4;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke()}
          for(var j=0;j<series.length;j++) {
            var points=series[j], active=false, last=0
            ctx.beginPath(); ctx.lineWidth=2; ctx.strokeStyle=j===0 ? Commons.Color.accent : (root.bar ? root.bar.foreground : Commons.Color.foreground)
            for(var k=0;k<points.length;k++) {
              var q=points[k]; if(!q || q[1]===null || !isFinite(q[1]) || q[0]<start || q[0]>end) {active=false;continue}
              var x=(q[0]-start)/span*width, yy=height-2-Math.max(0,Math.min(1,q[1]/maxY))*(height-4)
              if(!active || q[0]-last>3500) ctx.moveTo(x,yy); else ctx.lineTo(x,yy)
              active=true;last=q[0]
            }
            ctx.stroke()
          }
        }
      }

    }
  }
}
