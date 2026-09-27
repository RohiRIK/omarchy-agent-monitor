import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root
  visible: false

  property var settings: ({})

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string collectorPath: decodeURIComponent(Qt.resolvedUrl("collector.py").toString().replace("file://", ""))
  readonly property string usageDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/agent-monitor/usage"

  property var agentIds: []
  property var agents: []
  property int dataRevision: 0

  Process {
    id: listProcess
    running: false
    command: ["find", root.usageDir, "-maxdepth", "1", "-name", "*.json", "-printf", "%f\n"]

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyAgentListing(text)
    }
  }

  function rescanAgents() {
    if (!listProcess.running) listProcess.running = true
  }

  function applyAgentListing(output) {
    var ids = []
    var lines = String(output || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var name = lines[i].trim()
      if (name.slice(-5) === ".json") ids.push(name.slice(0, -5))
    }
    ids.sort()
    if (JSON.stringify(ids) !== JSON.stringify(agentIds)) agentIds = ids
  }

  Instantiator {
    id: agentInstantiator
    model: root.agentIds

    delegate: Agent {
      required property var modelData
      agentId: modelData
      path: root.usageDir + "/" + modelData + ".json"
      onRecordChanged: root.recordsChanged()
    }

    onObjectAdded: (index, object) => root.rebuildAgents()
    onObjectRemoved: (index, object) => root.rebuildAgents()
  }

  function rebuildAgents() {
    var result = []
    for (var i = 0; i < agentInstantiator.count; i++) {
      var agent = agentInstantiator.objectAt(i)
      if (agent) result.push(agent)
    }
    agents = result
    recordsChanged()
  }

  function recordsChanged() {
    dataRevision++
  }

  Component.onCompleted: {
    rescanAgents()
  }

  // -------------------------------------------------------------- refresh

  property int refreshIntervalSec: Math.max(30, Number(setting("refreshIntervalSec", 900)))
  readonly property bool refreshing: updateProcess.running
  property string pendingUpdateKind: ""

  Timer {
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: false
    onTriggered: root.runUpdate("normal")
  }

  Process {
    id: updateProcess
    running: false
    onExited: {
      root.rescanAgents()
      if (root.pendingUpdateKind !== "") {
        var kind = root.pendingUpdateKind
        root.pendingUpdateKind = ""
        root.runUpdate(kind)
      }
    }
  }

  function updateCommand(kind, agentIds) {
    var collectorScript = root.collectorPath
    var command = ["python3", collectorScript]
    if (kind === "force") command.push("--force")
    if (kind === "limits") command.push("--limits-only")
    if (agentIds) {
      for (var i = 0; i < agentIds.length; i++) command.push(agentIds[i])
    }
    return command
  }

  function runUpdate(kind, agentIds) {
    if (updateProcess.running) {
      if (kind === "force" || root.pendingUpdateKind === "") root.pendingUpdateKind = kind
      return
    }
    updateProcess.command = updateCommand(kind, agentIds)
    updateProcess.running = true
  }

  function refresh() { refreshAll(true) }
  function refreshAll(force) { runUpdate(force === true ? "force" : "normal") }
  function refreshLimits() { runUpdate("limits") }

  // ------------------------------------------------------------- providers

  function setMonitoring(id, enabled) {
    if (updateProcess.running) return
    updateProcess.command = ["python3", root.collectorPath, "--set-enabled", id, enabled ? "true" : "false"]
    updateProcess.running = true
  }

  property var allProviders: {
    var rev = dataRevision
    var result = []
    for (var i = 0; i < agents.length; i++) {
      var record = agents[i] ? agents[i].record : null
      if (record && record.id) result.push(displayProvider(record))
    }
    return result
  }

  property var enabledProviders: {
    var rev = dataRevision
    var result = []
    for (var i = 0; i < agents.length; i++) {
      var record = agents[i] ? agents[i].record : null
      if (!record || !record.id) continue
      var id = String(record.id)
      if (!providerEnabled(id) || record.monitoringEnabled === false) continue
      var display = displayProvider(record)
      if (providerHasData(display)) result.push(display)
    }
    return result
  }

  function providerEnabled(id) {
    if (!settings || !settings.providers || !settings.providers[id]) return true
    return settings.providers[id].enabled !== false
  }

  function numberValue(val) {
    var num = Number(val)
    return isFinite(num) ? num : 0
  }

  function providerHasData(p) {
    return p.installed || numberValue(p.totalPrompts) > 0 || numberValue(p.totalSessions) > 0
      || numberValue(p.activeDays) > 0 || numberValue(p.todayPrompts) > 0
      || numberValue(p.todaySessions) > 0 || (p.limits && p.limits.length > 0)
      || !!p.balance
  }

  function balanceValue(raw) {
    if (!raw || typeof raw !== "object") return null
    var remaining = Number(raw.remaining)
    var funded = Number(raw.funded)
    if (!isFinite(remaining) || remaining < 0) return null
    return {
      remaining: remaining,
      funded: isFinite(funded) && funded > 0 ? funded : 0,
      spent: Math.max(0, Number(raw.spent) || 0),
      currency: String(raw.currency || "USD"),
      estimated: raw.estimated === true
    }
  }

  function displayProvider(record) {
    return {
      providerId: String(record.id),
      providerName: String(record.name || record.id),
      ready: record.ready !== false,
      installed: record.installed === true,
      installationStatus: String(record.installationStatus || "Unknown"),
      monitoringEnabled: record.monitoringEnabled !== false,
      hasDailyTokens: record.hasDailyTokens !== false,
      dataNote: String(record.dataNote || ""),
      hasLocalStats: record.hasLocalStats !== false,
      usageStatusText: String(record.usageStatusText || ""),
      authHelpText: String(record.authHelpText || ""),
      limits: Array.isArray(record.limits) ? record.limits : [],
      tierLabel: String(record.tierLabel || ""),
      balance: balanceValue(record.balance),
      todayPrompts: numberValue(record.todayPrompts),
      todaySessions: numberValue(record.todaySessions),
      todayTotalTokens: numberValue(record.todayTotalTokens),
      totalPrompts: numberValue(record.totalPrompts),
      totalSessions: numberValue(record.totalSessions),
      activeDays: numberValue(record.activeDays),
      recentDays: Array.isArray(record.recentDays) ? record.recentDays : [],
      modelUsage: record.modelUsage && typeof record.modelUsage === "object" ? record.modelUsage : {},
      updatedAt: String(record.updatedAt || ""),
      hasPromptStats: record.hasPromptStats !== false
    }
  }

  function setting(key, fallback) {
    if (!settings || settings[key] === undefined) return fallback
    return settings[key]
  }

  function formatTokenCount(num) {
    var count = Number(num)
    if (!isFinite(count) || count <= 0) return "0"
    if (count >= 1000000000) return (count / 1000000000).toFixed(1) + "B"
    if (count >= 1000000) return (count / 1000000).toFixed(1) + "M"
    if (count >= 1000) return (count / 1000).toFixed(1) + "k"
    return String(Math.round(count))
  }

  function friendlyModelName(id) {
    return String(id || "Unknown model")
  }
}
