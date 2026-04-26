from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BETTER_CAREER = ROOT / "mods" / "better_career" / "better_career_mod_v0.5.0.zip"
DEFAULT_SERVER = ROOT / "mods" / "better_career" / "CareerMP_v0.0.31.zip"
DEFAULT_OUT_DIR = ROOT / "mods" / "generated"
DEFAULT_TEST_SERVER = ROOT / "servers" / "tests" / "better-career-careermp-west-coast"
DEFAULT_TEMPLATE_SERVER = ROOT / "servers" / "tests" / "west-coast"
UPSTREAM_CLIENT_URL = "https://raw.githubusercontent.com/StanleyDudek/CareerMP/main/Resources/Client/CareerMP.zip"

CLIENT_OUT_NAME = "CareerMP_BetterCareer.zip"
SERVER_OUT_NAME = "CareerMP_BetterCareer_Server.zip"
SERVER_PORT = 30831

CAREERMP_CLIENT_KEEP_PREFIXES = (
    "levels/west_coast_usa/main/MissionGroup/career_garage/",
    "levels/west_coast_usa/main/MissionGroup/DragRace/",
    "lua/ge/extensions/careerMP",
    "lua/ge/extensions/gameplay/drag/",
    "lua/ge/extensions/gameplay/walk.lua",
    "lua/vehicle/extensions/auto/careerMPEnabler.lua",
    "scripts/CareerMP/",
    "settings/ui_apps/layouts/default/careermp.uilayout.json",
    "ui/modules/apps/CareerMP-PlayerList/",
)

CAREERMP_CLIENT_EXCLUDE_PREFIXES = (
    "lua/ge/extensions/career/careerMP.lua",
    "lua/ge/extensions/career/modules/",
)

FORBIDDEN_ORIGINAL_CAREERMP_CLIENT_FILES = {
    "lua/ge/extensions/career/careerMP.lua",
    "lua/ge/extensions/career/modules/computer.lua",
    "lua/ge/extensions/career/modules/insurance/history.lua",
    "lua/ge/extensions/career/modules/insurance/insurance.lua",
    "lua/ge/extensions/career/modules/inventory.lua",
    "lua/ge/extensions/career/modules/painting.lua",
    "lua/ge/extensions/career/modules/partShopping.lua",
    "lua/ge/extensions/career/modules/playerDriving.lua",
    "lua/ge/extensions/career/modules/tuning.lua",
}


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_zip_entries(zip_path: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entries[info.filename.replace("\\", "/")] = zf.read(info.filename)
    return entries


def write_zip(entries: dict[str, bytes], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    compression = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(out_path, "w", compression=compression, compresslevel=9) as zf:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name)
            info.date_time = (2026, 4, 26, 12, 0, 0)
            info.compress_type = compression
            zf.writestr(info, entries[name])


def replace_once(text: str, old: str, new: str, path: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Could not patch {label} in {path}; expected source block was not found.")
    return text.replace(old, new, 1)


def download_upstream_client(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "CareerMP_upstream_original.zip"
    with urllib.request.urlopen(UPSTREAM_CLIENT_URL, timeout=60) as response:
        target.write_bytes(response.read())
    return target


def should_keep_careermp_entry(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in CAREERMP_CLIENT_EXCLUDE_PREFIXES):
        return False
    return any(path.startswith(prefix) for prefix in CAREERMP_CLIENT_KEEP_PREFIXES)


def patch_careermp_modscript(entries: dict[str, bytes]) -> None:
    path = "scripts/CareerMP/modScript.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")
    text = text.replace("\nload('/career/careerMP')\nsetExtensionUnloadMode('/career/careerMP', 'manual')\n", "\n")
    bootstrap = """-- Better Career must bootstrap before the CareerMP bridge.
-- BeamMP can execute mod scripts in an order where CareerMP starts first.
setExtensionUnloadMode("bcm_extensionManager", "manual")
load("bcm_extensionManager")

"""
    if "load(\"bcm_extensionManager\")" not in text:
        text = bootstrap + text
    if "/career/careerMP" in text:
        raise RuntimeError("CareerMP modScript still loads /career/careerMP.")
    entries[path] = text.encode("utf-8")


def patch_careermp_enabler(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/careerMPEnabler.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    text = replace_once(
        text,
        "local careerMPActive = false\nlocal syncRequested = false\n",
        "local careerMPActive = false\nlocal syncRequested = false\nlocal waitingForBetterCareer = false\n\n",
        path,
        "Better Career wait state",
    )

    helper = """local function getGlobalOrExtension(name)
\tlocal value = rawget(_G, name)
\tif value then
\t\treturn value
\tend
\tif extensions then
\t\treturn extensions[name]
\tend
\treturn nil
end

local function getBetterCareerCareer()
\tlocal career = rawget(_G, "career_career") or extensions.career_career
\tif career and type(career.createOrLoadCareerAndStart) == "function" then
\t\treturn career
\tend
\treturn nil
end

local function isBetterCareerBootReady()
\tlocal career = getBetterCareerCareer()
\tif not career then
\t\treturn false, "career_career"
\tend
\tif worldReadyState ~= nil and worldReadyState ~= 2 then
\t\treturn false, "worldReadyState=" .. tostring(worldReadyState)
\tend
\tif not getGlobalOrExtension("bcm_extensionManager") then
\t\tif extensions and extensions.load then
\t\t\tpcall(function() extensions.load("bcm_extensionManager") end)
\t\tend
\t\treturn false, "bcm_extensionManager"
\tend
\tif not getGlobalOrExtension("bcm_settings") or not getGlobalOrExtension("bcm_garages") or not getGlobalOrExtension("bcm_banking") then
\t\treturn false, "BCM core modules"
\tend
\treturn true, nil
end

local function startBetterCareerCareer()
\tif careerMPActive or not clientConfig then
\t\treturn true
\tend
\tlocal ready, reason = isBetterCareerBootReady()
\tif not ready then
\t\tif not waitingForBetterCareer then
\t\t\tlog("W", "careerMP", "Waiting for Better Career boot before starting CareerMP save: " .. tostring(reason))
\t\t\twaitingForBetterCareer = true
\t\tend
\t\treturn false
\tend
\tlocal career = getBetterCareerCareer()
\tif clientConfig.serverSaveNameEnabled then
\t\tnickname = clientConfig.serverSaveName
\tend
\tlocal saveName = nickname .. (clientConfig.serverSaveSuffix or "")
\tlocal currentLevel = getCurrentLevelIdentifier and getCurrentLevelIdentifier() or nil
\tcareer.createOrLoadCareerAndStart(saveName, false, false, nil, nil, nil, currentLevel)
\tcareerMPActive = true
\twaitingForBetterCareer = false
\tlog("W", "careerMP", "Started Better Career save through CareerMP bridge: " .. tostring(saveName))
\treturn true
end

"""
    text = replace_once(text, "--Settings\n\n", "--Settings\n\n" + helper, path, "Better Career starter helper")

    text = replace_once(
        text,
        "\tif not careerMPActive then\n"
        "\t\tif clientConfig.serverSaveNameEnabled then\n"
        "\t\t\tnickname = clientConfig.serverSaveName\n"
        "\t\tend\n"
        "\t\tcareer_career.createOrLoadCareerAndStart(nickname .. clientConfig.serverSaveSuffix, false, false)\n"
        "\t\tcareerMPActive = true\n"
        "\tend\n",
        "\tif not careerMPActive then\n"
        "\t\tstartBetterCareerCareer()\n"
        "\tend\n",
        path,
        "CareerMP save start",
    )

    text = replace_once(
        text,
        "local function onUpdate(dtReal, dtSim, dtRaw)\n\tpatchBeamMP()\n",
        "local function onUpdate(dtReal, dtSim, dtRaw)\n\tif clientConfig and not careerMPActive then\n\t\tstartBetterCareerCareer()\n\tend\n\tpatchBeamMP()\n",
        path,
        "Better Career retry on update",
    )

    text = replace_once(
        text,
        "local function rxClientConfigUpdate(data)\n"
        "\tclientConfig = jsonDecode(data)\n"
        "\tblockedInputActions = {}\n"
        "\tsettingsCheck()\n"
        "\tactionsCheck()\n"
        "end\n",
        "local function rxClientConfigUpdate(data)\n"
        "\tclientConfig = jsonDecode(data)\n"
        "\tblockedInputActions = {}\n"
        "\tsettingsCheck()\n"
        "\tactionsCheck()\n"
        "\tif not careerMPActive then\n"
        "\t\tstartBetterCareerCareer()\n"
        "\tend\n"
        "end\n",
        path,
        "restart Better Career on reconnect config update",
    )

    text = replace_once(
        text,
        "\tAddEventHandler(\"rxCareerVehSync\", rxCareerVehSync)\n"
        "\tAddEventHandler(\"rxTrafficSignalTimer\", rxTrafficSignalTimer)\n"
        "\tcareer_career = extensions.career_careerMP\n"
        "\tlog('W', 'careerMP', 'CareerMP Enabler LOADED!')\n",
        "\tAddEventHandler(\"rxCareerVehSync\", rxCareerVehSync)\n"
        "\tAddEventHandler(\"rxTrafficSignalTimer\", rxTrafficSignalTimer)\n"
        "\tlog('W', 'careerMP', 'CareerMP Enabler LOADED for Better Career bridge!')\n",
        path,
        "remove career_careerMP assignment",
    )

    text = replace_once(
        text,
        "local function onServerLeave()\n"
        "\tunPatchBeamMP()\n"
        "\tblockedInputActions = {}\n"
        "\textensions.core_input_actionFilter.setGroup('careerMP', blockedInputActions)\n"
        "\textensions.core_input_actionFilter.addAction(0, 'careerMP', false)\n"
        "\tsetTrafficSettings(userTrafficSettings)\n"
        "\tsetGameplaySettings(userGameplaySettings)\n"
        "end\n",
        "local function onServerLeave()\n"
        "\tunPatchBeamMP()\n"
        "\tclientConfig = nil\n"
        "\tcareerMPActive = false\n"
        "\tsyncRequested = false\n"
        "\twaitingForBetterCareer = false\n"
        "\tblockedInputActions = {}\n"
        "\textensions.core_input_actionFilter.setGroup('careerMP', blockedInputActions)\n"
        "\textensions.core_input_actionFilter.addAction(0, 'careerMP', false)\n"
        "\tsetTrafficSettings(userTrafficSettings)\n"
        "\tsetGameplaySettings(userGameplaySettings)\n"
        "\tlog('W', 'careerMP', 'CareerMP session reset after server leave; Better Career will restart on reconnect')\n"
        "end\n",
        path,
        "reset CareerMP Better Career bridge on server leave",
    )

    forbidden = (
        "career_career = extensions.career_careerMP",
        "extensions.career_careerMP",
        "career_career.createOrLoadCareerAndStart",
    )
    for token in forbidden:
        if token in text:
            raise RuntimeError(f"Patched CareerMP enabler still contains forbidden token: {token}")
    entries[path] = text.encode("utf-8")


def patch_better_career_player_driving(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/overrides/career/modules/playerDriving.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    helper = """local function getCareerMPClientConfig()
  if careerMPEnabler and type(careerMPEnabler.getClientConfig) == "function" then
    local ok, cfg = pcall(careerMPEnabler.getClientConfig)
    if ok and type(cfg) == "table" then
      return cfg
    end
  end
  return nil
end

"""
    text = replace_once(text, "local function setTrafficVars()\n", helper + "local function setTrafficVars()\n", path, "CareerMP config helper")

    old = """    -- traffic amount
    local amount = settings.getValue('trafficAmount')
    if amount == 0 then
      amount = gameplay_traffic.getIdealSpawnAmount()
    end
    if not getAllVehiclesByType()[1] then
      amount = amount - 1
    end
    if not M.debugMode then
      amount = clamp(amount, 2, 50)
    end

    -- parked cars amount
    local parkedAmount = settings.getValue('trafficParkedAmount')
    if parkedAmount == 0 then
      parkedAmount = clamp(gameplay_traffic.getIdealSpawnAmount(nil, true), 4, 20)
    end
    if not M.debugMode then
      parkedAmount = clamp(parkedAmount, 2, 50)
    end

    -- Police vehicles are spawned by bcm_police separately (via onTrafficStarted)
    local policeAmount = 0
    local extraAmount = 0
    playerData.trafficActive = math.huge

    gameplay_parking.setupVehicles(restrict and testTrafficAmounts.parkedCars or parkedAmount)

    local totalAmount = restrict and testTrafficAmounts.traffic + extraAmount or amount + extraAmount

    gameplay_traffic.setupTraffic(totalAmount, 0, {
      policeAmount = policeAmount,
      simpleVehs = true,
      autoLoadFromFile = true
    })
    setTrafficVars()

    log('I', 'bcm_playerDriving', 'Traffic spawned: ' .. totalAmount .. ' vehicles')
"""
    new = """    local careerMPConfig = getCareerMPClientConfig()
    local roadTrafficEnabled = not careerMPConfig or careerMPConfig.roadTrafficEnabled == true
    local parkedTrafficEnabled = not careerMPConfig or careerMPConfig.parkedTrafficEnabled == true

    -- traffic amount
    local amount = settings.getValue('trafficAmount')
    if amount == 0 then
      amount = gameplay_traffic.getIdealSpawnAmount()
    end
    if not getAllVehiclesByType()[1] then
      amount = amount - 1
    end
    if careerMPConfig and type(careerMPConfig.roadTrafficAmount) == "number" and careerMPConfig.roadTrafficAmount > 0 then
      amount = math.min(amount, careerMPConfig.roadTrafficAmount)
    end
    if not M.debugMode and roadTrafficEnabled then
      amount = clamp(amount, 2, 50)
    elseif not roadTrafficEnabled then
      amount = 0
    end

    -- parked cars amount
    local parkedAmount = settings.getValue('trafficParkedAmount')
    if parkedAmount == 0 then
      parkedAmount = clamp(gameplay_traffic.getIdealSpawnAmount(nil, true), 4, 20)
    end
    if careerMPConfig and type(careerMPConfig.parkedTrafficAmount) == "number" and careerMPConfig.parkedTrafficAmount > 0 then
      parkedAmount = math.min(parkedAmount, careerMPConfig.parkedTrafficAmount)
    end
    if not M.debugMode and parkedTrafficEnabled then
      parkedAmount = clamp(parkedAmount, 2, 50)
    elseif not parkedTrafficEnabled then
      parkedAmount = 0
    end

    -- Police vehicles are spawned by bcm_police separately (via onTrafficStarted)
    local policeAmount = 0
    local extraAmount = 0
    playerData.trafficActive = math.huge

    if parkedTrafficEnabled and parkedAmount > 0 then
      gameplay_parking.setupVehicles(restrict and testTrafficAmounts.parkedCars or parkedAmount)
    elseif gameplay_parking and gameplay_parking.deleteVehicles then
      gameplay_parking.deleteVehicles()
    end

    local totalAmount = restrict and testTrafficAmounts.traffic + extraAmount or amount + extraAmount

    if roadTrafficEnabled and totalAmount > 0 then
      gameplay_traffic.setupTraffic(totalAmount, 0, {
        policeAmount = policeAmount,
        simpleVehs = true,
        autoLoadFromFile = true
      })
    elseif gameplay_traffic and gameplay_traffic.deleteVehicles then
      gameplay_traffic.deleteVehicles()
    end
    setTrafficVars()

    log('I', 'bcm_playerDriving', 'Traffic configured by CareerMP: road=' .. tostring(roadTrafficEnabled) .. ' amount=' .. tostring(totalAmount) .. ' parked=' .. tostring(parkedTrafficEnabled) .. ' parkedAmount=' .. tostring(parkedAmount))
"""
    text = replace_once(text, old, new, path, "Better Career traffic config")
    entries[path] = text.encode("utf-8")


def patch_better_career_spawn_manager(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/career/modules/bcm_spawnManager.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    callback = (
        "local tut = extensions and extensions.bcm_tutorial or rawget(_G, 'bcm_tutorial'); "
        "if tut and tut.onMiramarSpawnFinished then tut.onMiramarSpawnFinished(%d) else "
        "log('W', 'bcm_spawnManager', 'bcm_tutorial unavailable for Miramar callback; skipping Miramar callback') end"
    )
    old = "\"partCondition.initConditions(nil, %d, nil, %f) obj:queueGameEngineLua('bcm_tutorial.onMiramarSpawnFinished(%d)')\""
    new = f"\"partCondition.initConditions(nil, %d, nil, %f) obj:queueGameEngineLua([[{callback}]])\""
    if old not in text:
        raise RuntimeError(f"Could not patch Miramar tutorial callback in {path}; source file layout changed.")
    text = text.replace(old, new)
    if "bcm_tutorial.onMiramarSpawnFinished" in text:
        raise RuntimeError("Patched spawn manager still contains unsafe bcm_tutorial callback.")
    text = replace_once(
        text,
        """      if not isSafe then
        obj:delete()
        log("I", logTag, "Removed vehicle (id: " .. objId .. ")")
        removed = true
      end
""",
        """      local isBeamMPUnicycle = false
      pcall(function()
        isBeamMPUnicycle = obj.getJBeamFilename and obj:getJBeamFilename() == "unicycle" and rawget(_G, "MPVehicleGE") ~= nil
      end)
      if isBeamMPUnicycle then
        isSafe = true
        log("I", logTag, "Keeping BeamMP walking vehicle (id: " .. objId .. ")")
      end
      if not isSafe then
        obj:delete()
        log("I", logTag, "Removed vehicle (id: " .. objId .. ")")
        removed = true
      end
""",
        path,
        "preserve BeamMP walking vehicle during Better Career spawn",
    )
    text = replace_once(
        text,
        "  gameplay_walk.setWalkingMode(true, pos, rot)\n",
        "  gameplay_walk.setWalkingMode(true, pos, rot, true)\n",
        path,
        "force starter walking spawn",
    )
    text = replace_once(
        text,
        "    gameplay_walk.setWalkingMode(true, TUTORIAL_PLAYER_POS, TUTORIAL_PLAYER_ROT)\n",
        "    gameplay_walk.setWalkingMode(true, TUTORIAL_PLAYER_POS, TUTORIAL_PLAYER_ROT, true)\n",
        path,
        "force tutorial walking spawn",
    )
    text = replace_once(
        text,
        """      local playerVehId = be:getPlayerVehicleID(0)
      if not playerVehId or playerVehId < 0 then
        log("W", logTag, "No player vehicle after career load, placing at spawn point")
        spawnAtStarterGarage()
        -- Exit loading screen since no vehicle will trigger onVehicleGroupSpawned
        exitLoadingScreen()
      else
        log("I", logTag, "Player vehicle found (id: " .. playerVehId .. "), position OK")
      end
""",
        """      local playerVehId = be:getPlayerVehicleID(0)
      local playerVeh = playerVehId and playerVehId >= 0 and be:getObjectByID(playerVehId) or nil
      local isBeamMPWalkingVehicle = false
      pcall(function()
        isBeamMPWalkingVehicle = playerVeh and playerVeh.getJBeamFilename and playerVeh:getJBeamFilename() == "unicycle" and rawget(_G, "MPVehicleGE") ~= nil
      end)
      if not playerVehId or playerVehId < 0 or isBeamMPWalkingVehicle then
        log("W", logTag, "BeamMP/no-player walking state after career load, placing at spawn point")
        spawnAtStarterGarage()
        -- Exit loading screen since no vehicle will trigger onVehicleGroupSpawned
        exitLoadingScreen()
      else
        log("I", logTag, "Player vehicle found (id: " .. playerVehId .. "), position OK")
      end
""",
        path,
        "anchor existing BeamMP walking save",
    )
    entries[path] = text.encode("utf-8")


def patch_better_career_career_save_guard(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/overrides/career/career.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    old = """  for _, module in ipairs(debugModules) do
    if module.getDebugMenuActive and module.___extensionName___ then
      data.debugModuleOpenStates[module.___extensionName___] = module.getDebugMenuActive()
    end
  end
"""
    new = """  for _, module in ipairs(debugModules) do
    if module and module.getDebugMenuActive then
      local moduleName = module.___extensionName___ or module.__extensionName__ or module.___extensionName or module.debugName
      if type(moduleName) == "string" and moduleName ~= "" then
        data.debugModuleOpenStates[moduleName] = module.getDebugMenuActive()
      else
        log("W", "bcm_career", "Skipping debug module open state with nil extension name")
      end
    end
  end
"""
    text = replace_once(text, old, new, path, "safe debug module open-state save")
    entries[path] = text.encode("utf-8")


def patch_careermp_walk(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/gameplay/walk.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    text = replace_once(
        text,
        """      if veh:getJBeamFilename() == "unicycle" and not veh:getActive() and MPVehicleGE.isOwn(veh:getID()) then
        unicycle = veh
        break
      end
""",
        """      local mpVehicleGE = rawget(_G, "MPVehicleGE")
      local isOwn = not mpVehicleGE or not mpVehicleGE.isOwn or mpVehicleGE.isOwn(veh:getID())
      if veh:getJBeamFilename() == "unicycle" and not veh:getActive() and isOwn then
        unicycle = veh
        break
      end
""",
        path,
        "guard MPVehicleGE unicycle ownership",
    )

    old = """-- pos and rot are optional
local function setWalkingMode(enabled, pos, rot, force)
  if (enabled == active) or ((not atParkingSpeed or not togglingEnabled or (core_replay.getState() == 'playback')) and not force) then
    return false, getPlayerUnicycle() and getPlayerUnicycle():getId()
  end
  if enabled then
    if not getPlayerUnicycle() then
      extensions.hook("onBeforeWalkingModeToggled", enabled)
      getOutOfVehicle(getPlayerVehicle(0), pos, rot)
    end
  else
    if vehicleInFront then
      extensions.hook("onBeforeWalkingModeToggled", enabled, vehicleInFront:getId())
      getInVehicle(vehicleInFront)
    else
      extensions.hook("onBeforeWalkingModeToggled", enabled)
      setUnicycleInactive(getPlayerUnicycle())
    end
  end
  local playerUnicycle = getPlayerUnicycle()
  if enabled then
    return playerUnicycle ~= nil, playerUnicycle and playerUnicycle:getId()
  else
    return not playerUnicycle, playerUnicycle and playerUnicycle:getId()
  end
end
"""
    new = """local function repositionUnicycle(unicycle, pos, rot)
  if not unicycle or not pos then return end
  local visibilityPoint = pos + ((rot and (rot * forward)) or forward)
  unicycle:setActive(1)
  spawn.safeTeleport(unicycle, pos, nil, nil, visibilityPoint, false)
  local camData = core_camera.getCameraDataById(unicycle:getId())
  if camData and camData.unicycle then
    local unicyclePos = unicycle:getPosition()
    local finalDir = (rot and (rot * forward)) or forward
    camData.unicycle:setCustomData({pos = unicyclePos, front = finalDir, up = up})
  end
  be:enterVehicle(0, unicycle)
end

-- pos and rot are optional
local function setWalkingMode(enabled, pos, rot, force)
  local currentUnicycle = getPlayerUnicycle()
  if enabled and currentUnicycle then
    active = true
    if pos then
      extensions.hook("onBeforeWalkingModeToggled", enabled)
      repositionUnicycle(currentUnicycle, pos, rot)
    end
    return true, currentUnicycle:getId()
  end
  if (enabled == active) or ((not atParkingSpeed or not togglingEnabled or (core_replay.getState() == 'playback')) and not force) then
    return false, currentUnicycle and currentUnicycle:getId()
  end
  if enabled then
    extensions.hook("onBeforeWalkingModeToggled", enabled)
    getOutOfVehicle(getPlayerVehicle(0), pos, rot)
  else
    if vehicleInFront then
      extensions.hook("onBeforeWalkingModeToggled", enabled, vehicleInFront:getId())
      getInVehicle(vehicleInFront)
    else
      extensions.hook("onBeforeWalkingModeToggled", enabled)
      local unicycle = getPlayerUnicycle()
      if unicycle then
        setUnicycleInactive(unicycle)
      end
    end
  end
  local playerUnicycle = getPlayerUnicycle()
  active = playerUnicycle ~= nil
  if enabled then
    return playerUnicycle ~= nil, playerUnicycle and playerUnicycle:getId()
  else
    return not playerUnicycle, playerUnicycle and playerUnicycle:getId()
  end
end
"""
    text = replace_once(text, old, new, path, "BeamMP walking reposition")
    entries[path] = text.encode("utf-8")


def patch_better_career_multimap_travel(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/bcm/multimap.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    old = """  -- Discover and serialize the full vehicle train via journal
  local playerVehId = be:getPlayerVehicleID(0)
  if playerVehId and playerVehId >= 0 then
"""
    new = """  -- Discover and serialize the full vehicle train via journal
  local playerVehId = be:getPlayerVehicleID(0)
  local isWalking = false
  pcall(function()
    if gameplay_walk and gameplay_walk.isWalking then
      isWalking = gameplay_walk.isWalking() == true
    end
  end)
  if isWalking then
    playerVehId = nil
  end
  if playerVehId and playerVehId >= 0 then
"""
    text = replace_once(text, old, new, path, "walking travel vehicle guard")
    entries[path] = text.encode("utf-8")


def patch_better_career_multimap_app(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/bcm/multimapApp.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    old = """  -- Pause simulation while picker is open
  simTimeAuthority.pause(true)
"""
    new = """  -- Close the vanilla activity prompt before showing BCM's picker.
  if guihooks and guihooks.trigger then
    guihooks.trigger('ChangeState', { state = 'play', params = {} })
  end

  -- BeamMP compatibility: never pause the local simulation just to show the
  -- picker. If the UI event is swallowed by the activity prompt/cache state,
  -- pause(true) leaves the player unable to walk.
  simTimeAuthority.pause(false)
"""
    text = replace_once(text, old, new, path, "travel picker activity prompt close")
    entries[path] = text.encode("utf-8")


def patch_better_career_facilities_travel(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/overrides/freeroam/facilities.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    old = """      if elem.type == "travelNode" then
        data.buttonLabel = "Travel"
        data.buttonFun = function()
          if bcm_multimapApp and bcm_multimapApp.showTravelPicker then
            bcm_multimapApp.showTravelPicker(elem.facility.travelNodeId)
          end
        end
        table.insert(activityData, data)
      end
"""
    new = """      if elem.type == "travelNode" then
        data.buttonLabel = "Travel"
        data.buttonFun = function()
          local nodeId = elem.facility and elem.facility.travelNodeId

          if guihooks and guihooks.trigger then
            guihooks.trigger('ChangeState', { state = 'play', params = {} })
          end

          if not nodeId then
            log('W', 'bcm_facilities', 'Travel node interaction without travelNodeId')
            if simTimeAuthority and simTimeAuthority.pause then simTimeAuthority.pause(false) end
            return
          end

          if not bcm_multimap and extensions and extensions.load then
            pcall(function() extensions.load("bcm_multimap") end)
          end
          if not bcm_multimapApp and extensions and extensions.load then
            pcall(function() extensions.load("bcm_multimapApp") end)
          end

          local destinations = nil
          if bcm_multimap then
            local currentMap = bcm_multimap.getCurrentMap and bcm_multimap.getCurrentMap()
            local graph = bcm_multimap.getGraph and bcm_multimap.getGraph()
            local node = graph and currentMap and graph[currentMap] and graph[currentMap].nodes and graph[currentMap].nodes[nodeId]
            if node then
              if bcm_multimap.setLastTriggeredNode then
                bcm_multimap.setLastTriggeredNode(nodeId)
              end
              if bcm_multimap.getReachableDestinations then
                destinations = bcm_multimap.getReachableDestinations(node) or {}
              end
            end
          end

          -- Single reachable destination: travel directly so the vanilla activity
          -- prompt cannot swallow BCM's picker event and leave walking paused.
          if destinations and #destinations == 1 and bcm_multimap and bcm_multimap.travelTo then
            local dest = destinations[1]
            bcm_multimap.travelTo(dest.targetMap, dest.targetNode, dest.toll or 0, dest.connectionType)
            return
          end

          if destinations and #destinations == 0 then
            log('W', 'bcm_facilities', 'No reachable destinations for travel node: ' .. tostring(nodeId))
            if bcm_multimap and bcm_multimap.cancelTravel then
              bcm_multimap.cancelTravel()
            elseif simTimeAuthority and simTimeAuthority.pause then
              simTimeAuthority.pause(false)
            end
            return
          end

          if bcm_multimapApp and bcm_multimapApp.showTravelPicker then
            bcm_multimapApp.showTravelPicker(nodeId)
            return
          end

          log('W', 'bcm_facilities', 'Travel picker unavailable for node: ' .. tostring(nodeId))
          if bcm_multimap and bcm_multimap.cancelTravel then
            bcm_multimap.cancelTravel()
          elseif simTimeAuthority and simTimeAuthority.pause then
            simTimeAuthority.pause(false)
          end
        end
        table.insert(activityData, data)
      end
"""
    text = replace_once(text, old, new, path, "travel node facility interaction")
    entries[path] = text.encode("utf-8")


def patch_server_entries(entries: dict[str, bytes], save_suffix: str) -> dict[str, object]:
    path = "Resources/Server/CareerMP/careerMP.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")
    text = replace_once(text, "\t\tautoUpdate = true,\n", "\t\tautoUpdate = false,\n", path, "server autoUpdate default")
    text = replace_once(
        text,
        "function onPlayerJoinHandler(player_id)\n"
        "\tloadedPrefabs[player_id] = {}\n"
        "end\n",
        "function onPlayerJoinHandler(player_id)\n"
        "\tloadedPrefabs[player_id] = {}\n"
        "\tMP.TriggerClientEventJson(player_id, \"rxCareerSync\", Config.client)\n"
        "\tMP.TriggerClientEventJson(player_id, \"rxCareerVehSync\", vehicleStates)\n"
        "\tprint(\"[CareerMP] ---------- Sent Better Career bridge sync to player \" .. tostring(player_id))\n"
        "end\n",
        path,
        "server sends Better Career bridge sync on join",
    )
    entries[path] = text.encode("utf-8")

    config = {
        "server": {
            "autoUpdate": False,
            "autoRestart": False,
            "longWindowMax": 10000,
            "shortWindowMax": 1000,
            "longWindowSeconds": 300,
            "shortWindowSeconds": 30,
            "allowTransactions": True,
            "sessionSendingMax": 100000,
            "sessionReceiveMax": 200000,
        },
        "client": {
            "allGhost": False,
            "unicycleGhost": True,
            "serverSaveName": "",
            "serverSaveSuffix": save_suffix,
            "serverSaveNameEnabled": False,
            "roadTrafficAmount": 0,
            "parkedTrafficAmount": 0,
            "roadTrafficEnabled": False,
            "parkedTrafficEnabled": False,
            "worldEditorEnabled": False,
            "consoleEnabled": False,
            "simplifyRemoteVehicles": False,
            "spawnVehicleIgnitionLevel": 0,
            "skipOtherPlayersVehicles": False,
            "trafficSmartSelections": True,
            "trafficSimpleVehicles": True,
            "trafficAllowMods": False,
        },
    }
    entries["Resources/Server/CareerMP/config/config.json"] = json.dumps(config, indent=2, sort_keys=True).encode("utf-8")
    entries["Resources/Server/CareerMP/versions/client.json"] = json.dumps({"major": 0, "minor": 0, "revision": 31}, indent=2).encode("utf-8")
    entries["Resources/Server/CareerMP/versions/server.json"] = json.dumps({"major": 0, "minor": 0, "revision": 31}, indent=2).encode("utf-8")
    return config


def build_artifacts(args: argparse.Namespace) -> dict[str, object]:
    better_career_zip = args.better_career
    server_zip = args.server
    out_dir = args.out_dir
    cache_dir = args.workspace / ".cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    client_zip = download_upstream_client(cache_dir)
    better_entries = read_zip_entries(better_career_zip)
    careermp_entries = read_zip_entries(client_zip)
    server_entries = read_zip_entries(server_zip)

    client_entries = dict(better_entries)
    for name, data in careermp_entries.items():
        if should_keep_careermp_entry(name):
            client_entries[name] = data

    patch_careermp_modscript(client_entries)
    patch_careermp_enabler(client_entries)
    patch_careermp_walk(client_entries)
    patch_better_career_career_save_guard(client_entries)
    patch_better_career_player_driving(client_entries)
    patch_better_career_spawn_manager(client_entries)
    patch_better_career_multimap_travel(client_entries)
    patch_better_career_multimap_app(client_entries)
    patch_better_career_facilities_travel(client_entries)

    for name in client_entries:
        if name in FORBIDDEN_ORIGINAL_CAREERMP_CLIENT_FILES:
            raise RuntimeError(f"Unexpected original CareerMP career replacement in client zip: {name}")

    server_config = patch_server_entries(server_entries, args.server_save_suffix)

    client_out = out_dir / CLIENT_OUT_NAME
    server_out = out_dir / SERVER_OUT_NAME
    write_zip(client_entries, client_out)
    write_zip(server_entries, server_out)

    for out in (client_out, server_out):
        with zipfile.ZipFile(out, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"{out} failed zip test at {bad}")

    return {
        "client_out": client_out,
        "server_out": server_out,
        "upstream_client": client_zip,
        "server_config": server_config,
        "client_entries": len(client_entries),
        "server_entries": len(server_entries),
    }


def copy_tree_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    ignore = shutil.ignore_patterns("Server.log", "Server.old.log", "mods.json")
    shutil.copytree(src, dst, ignore=ignore)


def patch_server_config(server_dir: Path, port: int) -> None:
    config = server_dir / "ServerConfig.toml"
    text = config.read_text(encoding="utf-8")
    text = re.sub(r"^Port = .*$", f"Port = {port}", text, flags=re.MULTILINE)
    text = re.sub(r'^Name = ".*"$', 'Name = "Better Career + CareerMP Test"', text, flags=re.MULTILINE)
    text = re.sub(r'^Description = ".*"$', 'Description = "Better Career + original CareerMP bridge validation"', text, flags=re.MULTILINE)
    config.write_text(text, encoding="utf-8", newline="\n")


def install_test_server(args: argparse.Namespace, artifacts: dict[str, object]) -> Path:
    test_server = args.test_server
    copy_tree_clean(args.template_server, test_server)
    patch_server_config(test_server, args.server_port)

    resources_dir = test_server / "Resources"
    if resources_dir.exists():
        shutil.rmtree(resources_dir)

    client_dir = test_server / "Resources" / "Client"
    server_dir = test_server / "Resources" / "Server"
    client_dir.mkdir(parents=True, exist_ok=True)
    server_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(artifacts["client_out"], client_dir / "CareerMP.zip")

    career_server_dir = server_dir / "CareerMP"
    with zipfile.ZipFile(artifacts["server_out"], "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("Resources/Server/"):
                rel = name.removeprefix("Resources/Server/")
                target = server_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info.filename))

    return test_server


def run_server_boot_check(server_dir: Path, timeout: int, port: int) -> dict[str, object]:
    exe = server_dir / "BeamMP-Server.exe"
    if not exe.exists():
        raise RuntimeError(f"BeamMP server executable not found: {exe}")

    for log_name in ("Server.log", "Server.old.log"):
        log_path = server_dir / log_name
        if log_path.exists():
            log_path.unlink()

    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(server_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    started_at = time.time()
    log_path = server_dir / "Server.log"
    log_text = ""
    try:
        while time.time() - started_at < timeout:
            if log_path.exists():
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                if "ALL SYSTEMS STARTED SUCCESSFULLY, EVERYTHING IS OKAY" in log_text:
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")

    checks = {
        "boot_ok": "ALL SYSTEMS STARTED SUCCESSFULLY, EVERYTHING IS OKAY" in log_text,
        "career_mp_loaded": "[CareerMP] ---------- CareerMP Loaded!" in log_text,
        "no_update_attempt": "CareerMP Update" not in log_text,
        "port": port,
        "log_path": str(log_path),
    }
    return checks


def validate_outputs(args: argparse.Namespace, artifacts: dict[str, object], test_server: Path, boot: dict[str, object] | None) -> dict[str, object]:
    client_out = artifacts["client_out"]
    server_out = artifacts["server_out"]
    with zipfile.ZipFile(client_out, "r") as zf:
        names = set(zf.namelist())
        required = {
            "scripts/better_career_mod/modScript.lua",
            "scripts/CareerMP/modScript.lua",
            "lua/ge/extensions/bcm/extensionManager.lua",
            "lua/ge/extensions/careerMPEnabler.lua",
            "lua/ge/extensions/careerMPPlayerPayments.lua",
            "lua/ge/extensions/careerMPPrefabSync.lua",
            "lua/ge/extensions/careerMPUIApps.lua",
            "lua/ge/extensions/gameplay/walk.lua",
            "lua/ge/extensions/overrides/career/modules/playerDriving.lua",
            "ui/modules/apps/CareerMP-PlayerList/app.js",
        }
        missing = sorted(required - names)
        forbidden = sorted(name for name in names if name in FORBIDDEN_ORIGINAL_CAREERMP_CLIENT_FILES)
        enabler = zf.read("lua/ge/extensions/careerMPEnabler.lua").decode("utf-8")
        player_driving = zf.read("lua/ge/extensions/overrides/career/modules/playerDriving.lua").decode("utf-8")
        modscript = zf.read("scripts/CareerMP/modScript.lua").decode("utf-8")
        spawn_manager = zf.read("lua/ge/extensions/career/modules/bcm_spawnManager.lua").decode("utf-8")
        career = zf.read("lua/ge/extensions/overrides/career/career.lua").decode("utf-8")
        walk = zf.read("lua/ge/extensions/gameplay/walk.lua").decode("utf-8")
        multimap = zf.read("lua/ge/extensions/bcm/multimap.lua").decode("utf-8")
        multimap_app = zf.read("lua/ge/extensions/bcm/multimapApp.lua").decode("utf-8")
        facilities = zf.read("lua/ge/extensions/overrides/freeroam/facilities.lua").decode("utf-8")

    with zipfile.ZipFile(server_out, "r") as zf:
        server_config = json.loads(zf.read("Resources/Server/CareerMP/config/config.json").decode("utf-8"))
        server_lua = zf.read("Resources/Server/CareerMP/careerMP.lua").decode("utf-8")

    validation = {
        "missing_required_client_entries": missing,
        "forbidden_career_replacements": forbidden,
        "enabler_uses_better_career": "startBetterCareerCareer" in enabler,
        "enabler_waits_for_bcm_boot": "isBetterCareerBootReady" in enabler,
        "modscript_bootstraps_better_career_first": 'load("bcm_extensionManager")' in modscript,
        "enabler_resets_on_server_leave": "CareerMP session reset after server leave" in enabler,
        "enabler_restarts_on_config_update": "local function rxClientConfigUpdate" in enabler and "\t\tstartBetterCareerCareer()\n\tend\nend\n\nlocal function onCareerActive" in enabler,
        "enabler_does_not_force_career_mp": "career_careerMP" not in enabler,
        "modscript_does_not_load_career_mp": "/career/careerMP" not in modscript,
        "careermp_walk_repositions_existing_unicycle": "repositionUnicycle" in walk,
        "spawn_manager_preserves_beammp_unicycle": "Keeping BeamMP walking vehicle" in spawn_manager,
        "spawn_manager_forces_walking_spawn": "setWalkingMode(true, pos, rot, true)" in spawn_manager,
        "spawn_manager_anchors_existing_beammp_walking_save": "BeamMP/no-player walking state after career load" in spawn_manager,
        "player_driving_respects_careermp_traffic": "Traffic configured by CareerMP" in player_driving,
        "spawn_manager_uses_guarded_tutorial_callback": "extensions.bcm_tutorial" in spawn_manager,
        "career_save_skips_nil_debug_module_names": "Skipping debug module open state with nil extension name" in career,
        "travel_button_directs_single_destination": "Single reachable destination: travel directly" in facilities,
        "travel_button_closes_activity_prompt": "ChangeState" in facilities and "ChangeState" in multimap_app,
        "multimap_app_keeps_beammp_unpaused": "BeamMP compatibility: never pause" in multimap_app,
        "multimap_treats_walking_as_foot_travel": "gameplay_walk.isWalking()" in multimap and "playerVehId = nil" in multimap,
        "server_autoupdate_disabled": server_config["server"]["autoUpdate"] is False,
        "server_sends_bridge_sync_on_join": "Sent Better Career bridge sync to player" in server_lua,
        "server_config_path": str(test_server / "Resources" / "Server" / "CareerMP" / "config" / "config.json"),
        "test_server": str(test_server),
        "boot": boot,
    }
    validation["ok"] = (
        not missing
        and not forbidden
        and validation["enabler_uses_better_career"]
        and validation["enabler_waits_for_bcm_boot"]
        and validation["modscript_bootstraps_better_career_first"]
        and validation["enabler_resets_on_server_leave"]
        and validation["enabler_restarts_on_config_update"]
        and validation["enabler_does_not_force_career_mp"]
        and validation["modscript_does_not_load_career_mp"]
        and validation["careermp_walk_repositions_existing_unicycle"]
        and validation["spawn_manager_preserves_beammp_unicycle"]
        and validation["spawn_manager_forces_walking_spawn"]
        and validation["spawn_manager_anchors_existing_beammp_walking_save"]
        and validation["player_driving_respects_careermp_traffic"]
        and validation["spawn_manager_uses_guarded_tutorial_callback"]
        and validation["career_save_skips_nil_debug_module_names"]
        and validation["travel_button_directs_single_destination"]
        and validation["travel_button_closes_activity_prompt"]
        and validation["multimap_app_keeps_beammp_unpaused"]
        and validation["multimap_treats_walking_as_foot_travel"]
        and validation["server_autoupdate_disabled"]
        and validation["server_sends_bridge_sync_on_join"]
        and (boot is None or (boot["boot_ok"] and boot["career_mp_loaded"] and boot["no_update_attempt"]))
    )
    return validation


def write_report(args: argparse.Namespace, artifacts: dict[str, object], validation: dict[str, object]) -> Path:
    report = {
        "client": {
            "path": str(artifacts["client_out"]),
            "sha256": sha256sum(artifacts["client_out"]),
            "size": Path(artifacts["client_out"]).stat().st_size,
            "entries": artifacts["client_entries"],
        },
        "server": {
            "path": str(artifacts["server_out"]),
            "sha256": sha256sum(artifacts["server_out"]),
            "size": Path(artifacts["server_out"]).stat().st_size,
            "entries": artifacts["server_entries"],
        },
        "sources": {
            "better_career": str(args.better_career),
            "server": str(args.server),
            "upstream_client_url": UPSTREAM_CLIENT_URL,
            "upstream_client_cache": str(artifacts["upstream_client"]),
        },
        "validation": validation,
    }
    report_path = args.workspace / "docs" / "build_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Better Career + original CareerMP compatibility artifacts.")
    parser.add_argument("--better-career", type=Path, default=DEFAULT_BETTER_CAREER)
    parser.add_argument("--server", type=Path, default=DEFAULT_SERVER)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--template-server", type=Path, default=DEFAULT_TEMPLATE_SERVER)
    parser.add_argument("--test-server", type=Path, default=DEFAULT_TEST_SERVER)
    parser.add_argument("--server-port", type=int, default=SERVER_PORT)
    parser.add_argument("--server-save-suffix", default="_BetterCareerMP")
    parser.add_argument("--skip-test-server", action="store_true")
    parser.add_argument("--boot-check", action="store_true")
    parser.add_argument("--boot-timeout", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifacts = build_artifacts(args)
    test_server = args.test_server
    if not args.skip_test_server:
        test_server = install_test_server(args, artifacts)

    boot = None
    if args.boot_check and not args.skip_test_server:
        boot = run_server_boot_check(test_server, args.boot_timeout, args.server_port)

    validation = validate_outputs(args, artifacts, test_server, boot)
    report_path = write_report(args, artifacts, validation)

    print(json.dumps({
        "client": str(artifacts["client_out"]),
        "server": str(artifacts["server_out"]),
        "test_server": str(test_server),
        "report": str(report_path),
        "validation_ok": validation["ok"],
        "boot": boot,
    }, indent=2))
    return 0 if validation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
