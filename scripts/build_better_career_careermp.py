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
UPSTREAM_CLIENT_URL = "https://raw.githubusercontent.com/StanleyDudek/CareerMP/main/Resources/Client/CareerMP.zip"

CLIENT_OUT_NAME = "CareerMP_BetterCareer.zip"
SERVER_OUT_NAME = "CareerMP_BetterCareer_Server.zip"
READY_TO_USE_OUT_NAME = "CareerMP_BetterCareer_ReadyToUse.zip"
READY_TO_USE_README = """# Better Career + CareerMP Ready To Use

This package is meant for server owners who want the simplest BeamMP install path.

## Install

1. Stop your BeamMP server.
2. Extract this zip into the same folder as `BeamMP-Server.exe`.
3. Confirm these files exist:
   - `Resources/Client/CareerMP.zip`
   - `Resources/Server/CareerMP/careerMP.lua`
   - `Resources/Server/CareerMP/config/config.json`
4. Start the BeamMP server again.

## Important

- Do not also install the standalone Better Career zip in `Resources/Client`.
- Do not also install another CareerMP client zip in `Resources/Client`.
- CareerMP auto-update is disabled in this package so the server does not replace the compatibility client.
- If you are replacing an older build, close BeamNG and BeamMP completely before reconnecting so the client reloads the new Lua/UI files.

## What Players Should See

- Better Career tutorial and phone flow should load.
- The computer garage purchase flow should open the Better Career UI instead of closing the prompt.
- CareerMP player list/payment UI should still be available in BeamMP.
"""

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
    if "/career/careerMP" in text:
        raise RuntimeError("CareerMP modScript still loads /career/careerMP.")
    entries[path] = text.encode("utf-8")


def patch_careermp_enabler(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/careerMPEnabler.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    text = replace_once(
        text,
        "local careerMPActive = false\nlocal syncRequested = false\n",
        "local careerMPActive = false\nlocal syncRequested = false\nlocal waitingForBetterCareer = false\nlocal betterCareerUIReloadRequested = false\nlocal betterCareerUIReloadWait = 0\n\n",
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
\tlocal requiredBetterCareerModules = {
\t\t"bcm_settings",
\t\t"bcm_timeSystem",
\t\t"bcm_phone",
\t\t"bcm_notifications",
\t\t"bcm_appRegistry",
\t\t"bcm_identity",
\t\t"bcm_contacts",
\t\t"bcm_email",
\t\t"bcm_chat",
\t\t"bcm_transactionCategories",
\t\t"bcm_banking",
\t\t"bcm_creditScore",
\t\t"bcm_loans",
\t\t"bcm_loanApp",
\t\t"bcm_sleepManager",
\t\t"bcm_weather",
\t\t"bcm_weatherForecast",
\t\t"bcm_weatherApp",
\t\t"bcm_breakingNews",
\t\t"bcm_properties",
\t\t"bcm_garages",
\t\t"bcm_parkingProtector",
\t\t"bcm_partsMetadata",
\t\t"bcm_partsOrders",
\t\t"bcm_partsShopApp",
\t\t"bcm_fixdProApp",
\t\t"bcm_garageManagerApp",
\t\t"bcm_paintApp",
\t\t"bcm_dynoApp",
\t\t"bcm_vehicleGalleryApp",
\t\t"bcm_realEstateApp",
\t\t"bcm_rentals",
\t\t"bcm_marketplaceApp",
\t\t"bcm_dealershipApp",
\t\t"bcm_negotiation",
\t\t"bcm_defects",
\t\t"bcm_police",
\t\t"bcm_heatSystem",
\t\t"bcm_heatApp",
\t\t"bcm_fines",
\t\t"bcm_policeHud",
\t\t"gameplay_police",
\t\t"bcm_policeDamage",
\t\t"bcm_contracts",
\t\t"bcm_virtualCargo",
\t\t"bcm_planex",
\t\t"bcm_planexApp",
\t\t"bcm_multimap",
\t\t"bcm_transitJournal",
\t\t"bcm_multimapApp",
\t\t"bcm_trailerCoupling",
\t\t"bcm_propsRemover",
\t\t"bcm_tutorial",
\t\t"bcm_clockApp",
\t\t"bcm_bankApp",
\t\t"bcm_settingsApp",
\t\t"bcm_calendarApp",
\t\t"bcm_walletApp",
\t\t"bcm_contactsApp",
\t\t"bcm_chatApp",
\t}
\tfor _, moduleName in ipairs(requiredBetterCareerModules) do
\t\tif not getGlobalOrExtension(moduleName) then
\t\t\tif extensions and extensions.load then
\t\t\t\tpcall(function() extensions.load(moduleName) end)
\t\t\tend
\t\t\treturn false, moduleName
\t\tend
\tend
\treturn true, nil
end

local function isBeamMPActive()
\treturn rawget(_G, "MPConfig") ~= nil or rawget(_G, "MPVehicleGE") ~= nil
end

local function reloadBetterCareerUIOnce()
\tif betterCareerUIReloadRequested or not isBeamMPActive() then
\t\treturn false
\tend
\tif type(reloadUI) ~= "function" then
\t\treturn false
\tend
\tbetterCareerUIReloadRequested = true
\tbetterCareerUIReloadWait = 0
\tlog("W", "careerMP", "Reloading UI once so Better Career Vue assets mount after BeamMP mod download")
\tpcall(function() reloadUI() end)
\treturn true
end

local function sanitizeSaveNamePart(value, fallback)
	local cleaned = tostring(value or ""):gsub("[^%w_%-]", "_")
	cleaned = cleaned:gsub("_+", "_"):gsub("^_+", ""):gsub("_+$", "")
	if cleaned == "" then
		return fallback or "CareerMPGuest"
	end
	return cleaned
end

local function isGuestNickname(value)
	return type(value) == "string" and value:match("^guest%d+$") ~= nil
end

local function saveSlotUsesSuffix(slotName, suffix)
	return type(slotName) == "string" and suffix ~= "" and slotName:sub(-#suffix) == suffix
end

local function findLatestGuestSaveBaseNameForSuffix(suffix)
	if suffix == "" then
		return nil
	end
	local saveSystem = getGlobalOrExtension("career_saveSystem")
	if not saveSystem or type(saveSystem.getAllSaveSlots) ~= "function" then
		return nil
	end
	local ok, slots = pcall(saveSystem.getAllSaveSlots)
	if not ok or type(slots) ~= "table" then
		return nil
	end

	local newestBaseName
	local newestDate = ""
	for _, slotName in ipairs(slots) do
		if saveSlotUsesSuffix(slotName, suffix) then
			local baseName = slotName:sub(1, #slotName - #suffix)
			if isGuestNickname(baseName) then
				local date = "0"
				if type(saveSystem.getAutosave) == "function" then
					local autoOk, _, autosaveDate = pcall(saveSystem.getAutosave, slotName, false)
					if autoOk and autosaveDate then
						date = tostring(autosaveDate)
					end
				end
				if not newestBaseName or date > newestDate then
					newestBaseName = baseName
					newestDate = date
				end
			end
		end
	end

	return newestBaseName
end

local function writeGuestIdentity(path, data)
	local ok, result = pcall(function()
		return jsonWriteFile(path, data, true)
	end)
	if not ok or not result then
		log("E", "careerMP", "Failed to persist Better Career guest save identity: " .. tostring(path))
		return false
	end
	return true
end

local function getStableGuestSaveBaseName()
	local suffix = clientConfig and clientConfig.serverSaveSuffix or ""
	local key = sanitizeSaveNamePart(suffix ~= "" and suffix or "default", "default")
	local dir = "settings/careerMPBetterCareer"
	local path = dir .. "/guestSaveIdentity.json"
	if FS and type(FS.directoryExists) == "function" and not FS:directoryExists(dir) and type(FS.directoryCreate) == "function" then
		FS:directoryCreate(dir)
	end

	local data = jsonReadFile(path) or {}
	if type(data) ~= "table" then
		data = {}
	end
	if type(data.servers) ~= "table" then
		data.servers = {}
	end

	local storedBaseName = data.servers[key]
	if type(storedBaseName) == "string" and storedBaseName ~= "" then
		return sanitizeSaveNamePart(storedBaseName, "CareerMPGuest")
	end

	local migratedBaseName = findLatestGuestSaveBaseNameForSuffix(suffix)
	if migratedBaseName then
		data.servers[key] = migratedBaseName
		writeGuestIdentity(path, data)
		log("W", "careerMP", "Reusing existing BeamMP guest Better Career save identity: " .. migratedBaseName)
		return migratedBaseName
	end

	local seed = tostring(os.time()) .. "_" .. tostring(math.random(100000, 999999))
	local newBaseName = sanitizeSaveNamePart("guestLocal_" .. seed, "CareerMPGuest")
	data.servers[key] = newBaseName
	writeGuestIdentity(path, data)
	log("W", "careerMP", "Created stable Better Career guest save identity: " .. newBaseName)
	return newBaseName
end

local function resolveBetterCareerSaveName()
	local suffix = clientConfig and clientConfig.serverSaveSuffix or ""
	local baseName = nickname
	if clientConfig and clientConfig.serverSaveNameEnabled then
		baseName = clientConfig.serverSaveName
	elseif isGuestNickname(baseName) then
		baseName = getStableGuestSaveBaseName()
	end
	baseName = sanitizeSaveNamePart(baseName, "CareerMPGuest")
	return baseName .. suffix
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
\tif reloadBetterCareerUIOnce() then
\t\treturn false
\tend
\tif betterCareerUIReloadRequested and betterCareerUIReloadWait < 2 then
\t\treturn false
\tend
\tlocal career = getBetterCareerCareer()
\tlocal saveName = resolveBetterCareerSaveName()
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
        "local function onUpdate(dtReal, dtSim, dtRaw)\n\tif betterCareerUIReloadRequested and not careerMPActive then\n\t\tbetterCareerUIReloadWait = betterCareerUIReloadWait + (dtReal or 0)\n\tend\n\tif clientConfig and not careerMPActive then\n\t\tstartBetterCareerCareer()\n\tend\n\tpatchBeamMP()\n",
        path,
        "Better Career retry on update",
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
        """  log("I", logTag, "=== Initializing new career spawn ===")

  -- 1. Remove default-spawned vehicles (Covet etc.)
""",
        """  log("I", logTag, "=== Initializing new career spawn ===")

  -- BeamMP can start the save while Better Career's late first-run modules are
  -- still being reloaded after level start. Force the two tutorial gatekeepers
  -- to be present before deciding between tutorial spawn and garage fallback.
  if rawget(_G, "MPVehicleGE") ~= nil then
    for _, moduleName in ipairs({"bcm_identity", "bcm_tutorial"}) do
      if not rawget(_G, moduleName) and extensions and extensions.load then
        pcall(function() extensions.load(moduleName) end)
      end
    end
    if bcm_identity and bcm_identity.onCareerModulesActivated then
      pcall(function() bcm_identity.onCareerModulesActivated() end)
    end
    if bcm_tutorial and bcm_tutorial.onCareerActive then
      pcall(function() bcm_tutorial.onCareerActive(true) end)
    end
  end

  -- 1. Remove default-spawned vehicles (Covet etc.)
""",
        path,
        "ensure Better Career first-run modules before spawn",
    )
    text = replace_once(
        text,
        """  -- 7. Emit hook for tutorial system
  extensions.hook("onFirstCareerStart")
  log("I", logTag, "=== First career start complete ===")
""",
        """  -- 7. Emit hook for tutorial system. BeamMP may reload the extension during
  -- MP setup, so also call the module directly when it is available.
  extensions.hook("onFirstCareerStart")
  if bcm_tutorial and bcm_tutorial.onFirstCareerStart then
    pcall(function() bcm_tutorial.onFirstCareerStart() end)
  end
  if bcm_identity and bcm_identity.sendIdentityToUI then
    pcall(function() bcm_identity.sendIdentityToUI() end)
  end
  log("I", logTag, "=== First career start complete ===")
""",
        path,
        "direct BeamMP first-career tutorial hook",
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


def patch_better_career_identity(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/bcm/identity.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    text = replace_once(
        text,
        """local onSaveCurrentSaveSlot
local onBeforeSetSaveSlot
""",
        """local onSaveCurrentSaveSlot
local onBeforeSetSaveSlot
local onUpdate
local ensureBeamMPFallbackIdentity
""",
        path,
        "identity retry forward declaration",
    )
    text = replace_once(
        text,
        """local identityData = nil
local activated = false
""",
        """local identityData = nil
local activated = false
local modalRetryTimer = 0
local modalRetryAttempts = 0
""",
        path,
        "identity retry state",
    )
    text = replace_once(
        text,
        """  guihooks.trigger('BCMIdentityUpdate', identityData)
  guihooks.trigger('BCMIdentityModalDone', {})
""",
        """  modalRetryTimer = 0
  modalRetryAttempts = 0
  guihooks.trigger('BCMIdentityUpdate', identityData)
  guihooks.trigger('BCMIdentityModalDone', {})
""",
        path,
        "identity retry reset after submit",
    )
    text = replace_once(
        text,
        """onBeforeSetSaveSlot = function()
  identityData = nil
  activated = false
  guihooks.trigger('BCMIdentityReset', {})
  log('D', 'bcm_identity', 'Identity state reset (save slot change)')
end
""",
        """onBeforeSetSaveSlot = function()
  identityData = nil
  activated = false
  modalRetryTimer = 0
  modalRetryAttempts = 0
  guihooks.trigger('BCMIdentityReset', {})
  log('D', 'bcm_identity', 'Identity state reset (save slot change)')
end

ensureBeamMPFallbackIdentity = function()
  if identityData then return true end
  if rawget(_G, "MPConfig") == nil and rawget(_G, "MPVehicleGE") == nil then return false end

  local playerName = "BeamMP"
  local mpConfig = rawget(_G, "MPConfig")
  if mpConfig and type(mpConfig.getNickname) == "function" then
    local ok, nickname = pcall(mpConfig.getNickname)
    if ok and nickname and tostring(nickname) ~= "" then
      playerName = tostring(nickname)
    end
  end

  local firstName = tostring(playerName):gsub("[^%w_%-]", "")
  if firstName == "" then firstName = "BeamMP" end
  local payload = {
    firstName = firstName,
    lastName = "Driver",
    sex = "other",
    birthday = "01/01/2000",
    rejectionCount = 0
  }

  log('W', 'bcm_identity', 'BeamMP identity form did not mount; auto-generating identity for ' .. firstName)
  return setIdentity(jsonEncode(payload))
end

onUpdate = function(dtReal)
  if not activated or identityData then return end
  if not career_career or not career_career.isActive or not career_career.isActive() then return end

  modalRetryTimer = modalRetryTimer + (dtReal or 0)
  if modalRetryTimer >= 3 then
    modalRetryTimer = 0
    modalRetryAttempts = modalRetryAttempts + 1
    log('I', 'bcm_identity', 'Identity modal retry for BeamMP late UI')
    guihooks.trigger('BCMShowIdentityModal', {})
    if modalRetryAttempts >= 4 then
      ensureBeamMPFallbackIdentity()
    end
  end
end
""",
        path,
        "identity modal retry for BeamMP",
    )
    text = replace_once(
        text,
        """M.onCareerModulesActivated = onCareerModulesActivated
M.onSaveCurrentSaveSlot = onSaveCurrentSaveSlot
M.onBeforeSetSaveSlot = onBeforeSetSaveSlot
""",
        """M.onCareerModulesActivated = onCareerModulesActivated
M.onSaveCurrentSaveSlot = onSaveCurrentSaveSlot
M.onBeforeSetSaveSlot = onBeforeSetSaveSlot
M.onUpdate = onUpdate
M.ensureBeamMPFallbackIdentity = ensureBeamMPFallbackIdentity
""",
        path,
        "identity retry public hook",
    )
    entries[path] = text.encode("utf-8")


def patch_better_career_tutorial(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/bcm/tutorial.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    marker = "M.onExtensionLoaded = function()\n"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"Could not patch recover missed BeamMP tutorial start in {path}; onExtensionLoaded was not found.")

    insert_after = "    onCareerActive(true)\n"
    insert_at = text.find(insert_after, start)
    if insert_at < 0:
        raise RuntimeError(
            f"Could not patch recover missed BeamMP tutorial start in {path}; onCareerActive reload hook was not found."
        )
    insert_at += len(insert_after)

    recovery = """

    -- BeamMP can reload bcm_tutorial after bcm_spawnManager has already emitted
    -- onFirstCareerStart. Recover that missed hook so the D.U.M.B. identity
    -- modal and step 1 tasklist appear instead of leaving currentStep at 0.
    if tutorialData
      and not tutorialData.tutorialDone
      and not tutorialData.tutorialSkipped
      and (tutorialData.currentStep or 0) == 0 then
      log('W', logTag, 'Recovering missed first-career tutorial start after BeamMP reload')
      onFirstCareerStart()
      if bcm_identity and bcm_identity.sendIdentityToUI then
        pcall(function() bcm_identity.sendIdentityToUI() end)
      end
    end"""
    if recovery.strip() not in text:
        text = text[:insert_at] + recovery + text[insert_at:]
    entries[path] = text.encode("utf-8")
    return

    text = replace_once(
        text,
        """M.onExtensionLoaded = function()
  if career_career and career_career.isActive() then
    log('I', logTag, 'Extension reloaded while career active — re-initializing')
    onCareerActive(true)
  end
end
""",
        """M.onExtensionLoaded = function()
  if career_career and career_career.isActive() then
    log('I', logTag, 'Extension reloaded while career active — re-initializing')
    onCareerActive(true)

    -- BeamMP can reload bcm_tutorial after bcm_spawnManager has already emitted
    -- onFirstCareerStart. Recover that missed hook so the D.U.M.B. identity
    -- modal and step 1 tasklist appear instead of leaving currentStep at 0.
    if tutorialData
      and not tutorialData.tutorialDone
      and not tutorialData.tutorialSkipped
      and (tutorialData.currentStep or 0) == 0 then
      log('W', logTag, 'Recovering missed first-career tutorial start after BeamMP reload')
      onFirstCareerStart()
      if bcm_identity and bcm_identity.sendIdentityToUI then
        pcall(function() bcm_identity.sendIdentityToUI() end)
      end
    end
  end
end
""",
        path,
        "recover missed BeamMP tutorial start",
    )
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


def patch_careermp_per_part_paint(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/careerMPPerPartPaint.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    text = replace_once(
        text,
        """local pendingPaints = {}
local pendingRemotePaints = {}
local ensuredPartConditionsByVeh = {}
""",
        """local pendingPaints = {}
local pendingRemotePaints = {}
local ensuredPartConditionsByVeh = {}
local maxPendingPaintAttempts = 120

local function getPendingInventoryId(entry)
\tif type(entry) == "table" then
\t\treturn entry.inventoryId
\tend
\treturn entry
end

local function queuePendingPartPaint(inventoryId, serverVehicleID, originID)
\tif not inventoryId then
\t\treturn
\tend
\tfor _, entry in ipairs(pendingPaints) do
\t\tlocal entryInventoryId = getPendingInventoryId(entry)
\t\tlocal entryServerVehicleID = type(entry) == "table" and entry.serverVehicleID or nil
\t\tlocal entryOriginID = type(entry) == "table" and entry.originID or nil
\t\tif entryInventoryId == inventoryId and entryServerVehicleID == serverVehicleID and entryOriginID == originID then
\t\t\treturn
\t\tend
\tend
\ttable.insert(pendingPaints, {
\t\tinventoryId = inventoryId,
\t\tserverVehicleID = serverVehicleID,
\t\toriginID = originID,
\t\tattempts = 0
\t})
end
""",
        path,
        "pending paint retry helpers",
    )

    old = """local function sendPartPaints(inventoryId, serverVehicleID, originID)
\tlocal partConditions = career_modules_inventory.getVehicles()[inventoryId].partConditions
\tfor part, partData in pairs(partConditions) do
\t\tif partData.visualState then
\t\t\tlocal data = {}
\t\t\tdata.partPath = part
\t\t\tdata.slotPath, data.partName = string.match(data.partPath, "(.*/)([^/]+)$")
\t\t\tdata.paints = partData.visualState.paint.originalPaints
\t\t\tdata.serverVehicleID = serverVehicleID
\t\t\tif originID\tthen
\t\t\t\tdata.originID = originID
\t\t\tend
\t\t\tTriggerServerEvent("perPartPainting", jsonEncode(data))
\t\tend
\tend
end
"""
    new = """local function sendPartPaints(inventoryId, serverVehicleID, originID, fromPending)
\tif not career_modules_inventory or not career_modules_inventory.getVehicles then
\t\tif not fromPending then
\t\t\tqueuePendingPartPaint(inventoryId, serverVehicleID, originID)
\t\tend
\t\treturn false
\tend

\tlocal vehicles = career_modules_inventory.getVehicles()
\tlocal vehicle = vehicles and vehicles[inventoryId] or nil
\tif not vehicle then
\t\tif not fromPending then
\t\t\tqueuePendingPartPaint(inventoryId, serverVehicleID, originID)
\t\tend
\t\treturn false
\tend

\tlocal partConditions = vehicle.partConditions
\tif type(partConditions) ~= "table" then
\t\tif not fromPending then
\t\t\tqueuePendingPartPaint(inventoryId, serverVehicleID, originID)
\t\tend
\t\tlog('D', 'careerMP', 'perPartPaint: inventoryId ' .. tostring(inventoryId) .. ' has no partConditions yet; deferring paint sync')
\t\treturn false
\tend

\tfor part, partData in pairs(partConditions) do
\t\tlocal paintState = partData and partData.visualState and partData.visualState.paint
\t\tif paintState and paintState.originalPaints then
\t\t\tlocal data = {}
\t\t\tdata.partPath = part
\t\t\tdata.slotPath, data.partName = string.match(data.partPath, "(.*/)([^/]+)$")
\t\t\tdata.paints = paintState.originalPaints
\t\t\tdata.serverVehicleID = serverVehicleID
\t\t\tif originID\tthen
\t\t\t\tdata.originID = originID
\t\t\tend
\t\t\tTriggerServerEvent("perPartPainting", jsonEncode(data))
\t\tend
\tend
\treturn true
end
"""
    text = replace_once(text, old, new, path, "guard missing inventory part conditions")

    old = """local function onInventorySpawnVehicle(inventoryId, gameVehicleID)
\tif gameVehicleID then
\t\tlocal vehicles = MPVehicleGE.getVehicles()
\t\tfor serverVehicleID, vehicleData in pairs(vehicles) do
\t\t\tif vehicleData.gameVehicleID and vehicleData.gameVehicleID == gameVehicleID then
\t\t\t\tsendPartPaints(inventoryId, serverVehicleID)
\t\t\telse
\t\t\t\ttable.insert(pendingPaints, inventoryId)
\t\t\tend
\t\tend
\telse
\t\ttable.insert(pendingPaints, inventoryId)
\tend
end
"""
    new = """local function onInventorySpawnVehicle(inventoryId, gameVehicleID)
\tif gameVehicleID then
\t\tlocal vehicles = MPVehicleGE.getVehicles()
\t\tfor serverVehicleID, vehicleData in pairs(vehicles) do
\t\t\tif vehicleData.gameVehicleID and vehicleData.gameVehicleID == gameVehicleID then
\t\t\t\tsendPartPaints(inventoryId, serverVehicleID)
\t\t\telse
\t\t\t\tqueuePendingPartPaint(inventoryId, nil, nil)
\t\t\tend
\t\tend
\telse
\t\tqueuePendingPartPaint(inventoryId, nil, nil)
\tend
end
"""
    text = replace_once(text, old, new, path, "use deduplicated pending paint queue")

    old = """\t\tfor i = #pendingPaints, 1, -1 do
\t\t\tlocal entry = pendingPaints[i]
\t\t\tlocal gameVehicleID = career_modules_inventory.getVehicleIdFromInventoryId(entry)
\t\t\tif gameVehicleID then
\t\t\t\tvehicles = MPVehicleGE.getVehicles()
\t\t\t\tfor serverVehicleID, vehicleData in pairs(vehicles) do
\t\t\t\t\tif vehicleData.gameVehicleID == gameVehicleID then
\t\t\t\t\t\tsendPartPaints(entry, serverVehicleID)
\t\t\t\t\t\ttable.remove(pendingPaints, i)
\t\t\t\t\tend
\t\t\t\tend
\t\t\tend
\t\tend
"""
    new = """\t\tfor i = #pendingPaints, 1, -1 do
\t\t\tlocal entry = pendingPaints[i]
\t\t\tlocal inventoryId = getPendingInventoryId(entry)
\t\t\tlocal serverVehicleID = type(entry) == "table" and entry.serverVehicleID or nil
\t\t\tlocal originID = type(entry) == "table" and entry.originID or nil
\t\t\tlocal attempts = type(entry) == "table" and (entry.attempts or 0) or 0
\t\t\tif not serverVehicleID and career_modules_inventory and career_modules_inventory.getVehicleIdFromInventoryId then
\t\t\t\tlocal gameVehicleID = career_modules_inventory.getVehicleIdFromInventoryId(inventoryId)
\t\t\t\tif gameVehicleID then
\t\t\t\t\tvehicles = MPVehicleGE.getVehicles()
\t\t\t\t\tfor foundServerVehicleID, vehicleData in pairs(vehicles) do
\t\t\t\t\t\tif vehicleData.gameVehicleID == gameVehicleID then
\t\t\t\t\t\t\tserverVehicleID = foundServerVehicleID
\t\t\t\t\t\t\tbreak
\t\t\t\t\t\tend
\t\t\t\t\tend
\t\t\t\tend
\t\t\tend

\t\t\tif serverVehicleID then
\t\t\t\tlocal ok = sendPartPaints(inventoryId, serverVehicleID, originID, true)
\t\t\t\tif ok or attempts >= maxPendingPaintAttempts then
\t\t\t\t\ttable.remove(pendingPaints, i)
\t\t\t\telse
\t\t\t\t\tpendingPaints[i] = {
\t\t\t\t\t\tinventoryId = inventoryId,
\t\t\t\t\t\tserverVehicleID = serverVehicleID,
\t\t\t\t\t\toriginID = originID,
\t\t\t\t\t\tattempts = attempts + 1
\t\t\t\t\t}
\t\t\t\tend
\t\t\telseif attempts >= maxPendingPaintAttempts then
\t\t\t\ttable.remove(pendingPaints, i)
\t\t\telseif type(entry) == "table" then
\t\t\t\tentry.attempts = attempts + 1
\t\t\telse
\t\t\t\tpendingPaints[i] = { inventoryId = inventoryId, attempts = attempts + 1 }
\t\t\tend
\t\tend
"""
    text = replace_once(text, old, new, path, "retry pending paints until Better Career inventory is hydrated")
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


def patch_better_career_garages(entries: dict[str, bytes]) -> None:
    path = "lua/ge/extensions/bcm/garages.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")

    old = """grantStarterGarageIfNeeded = function()
  if getGarageCount() > 0 then
    log('D', 'bcm_garages', 'grantStarterGarageIfNeeded: Player already has garages â€” skipping')
    return
  end

  -- Find the garage marked as starter in config
  local starterId = nil
  for garageId, definition in pairs(bcmGarageConfig) do
    if definition.isStarterGarage == true then
      starterId = garageId
      break
    end
  end

  if starterId then
    purchaseBcmGarage(starterId)
    log('I', 'bcm_garages', 'grantStarterGarageIfNeeded: Starter garage granted: ' .. starterId)
  else
    log('W', 'bcm_garages', 'grantStarterGarageIfNeeded: No starter garage found in config')
  end
end
"""
    new = """grantStarterGarageIfNeeded = function()
  if not bcm_properties then
    log('W', 'bcm_garages', 'grantStarterGarageIfNeeded: bcm_properties not available, deferring starter garage grant')
    return
  end

  if getGarageCount() > 0 then
    log('D', 'bcm_garages', 'grantStarterGarageIfNeeded: Player already has garages - skipping')
    return
  end

  -- Find the garage marked as starter in config
  local starterId = nil
  for garageId, definition in pairs(bcmGarageConfig) do
    if definition.isStarterGarage == true then
      starterId = garageId
      break
    end
  end

  if starterId then
    local record = purchaseBcmGarage(starterId)
    if record then
      log('I', 'bcm_garages', 'grantStarterGarageIfNeeded: Starter garage granted: ' .. starterId)
    else
      log('W', 'bcm_garages', 'grantStarterGarageIfNeeded: purchase failed for starter garage: ' .. starterId)
    end
  else
    log('W', 'bcm_garages', 'grantStarterGarageIfNeeded: No starter garage found in config')
  end
end
"""
    text = replace_once(text, old, new, path, "starter garage property guard")
    entries[path] = text.encode("utf-8")


def patch_server_entries(entries: dict[str, bytes]) -> dict[str, object]:
    path = "Resources/Server/CareerMP/careerMP.lua"
    text = entries[path].decode("utf-8").replace("\r\n", "\n")
    text = replace_once(text, "\t\tautoUpdate = true,\n", "\t\tautoUpdate = false,\n", path, "server autoUpdate default")
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
            "serverSaveSuffix": "_BetterCareerMP",
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


def build_ready_to_use_entries(client_out: Path, server_entries: dict[str, bytes]) -> dict[str, bytes]:
    ready_entries = {
        "README_READY_TO_USE.md": READY_TO_USE_README.encode("utf-8"),
        "Resources/Client/CareerMP.zip": client_out.read_bytes(),
    }
    for name, data in server_entries.items():
        normalized = name.replace("\\", "/")
        if normalized.startswith("Resources/Server/CareerMP/"):
            ready_entries[normalized] = data
    return ready_entries


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
    patch_careermp_per_part_paint(client_entries)
    patch_better_career_player_driving(client_entries)
    patch_better_career_spawn_manager(client_entries)
    patch_better_career_identity(client_entries)
    patch_better_career_tutorial(client_entries)
    patch_better_career_multimap_travel(client_entries)
    patch_better_career_multimap_app(client_entries)
    patch_better_career_facilities_travel(client_entries)
    patch_better_career_garages(client_entries)

    for name in client_entries:
        if name in FORBIDDEN_ORIGINAL_CAREERMP_CLIENT_FILES:
            raise RuntimeError(f"Unexpected original CareerMP career replacement in client zip: {name}")

    server_config = patch_server_entries(server_entries)

    client_out = out_dir / CLIENT_OUT_NAME
    server_out = out_dir / SERVER_OUT_NAME
    ready_to_use_out = out_dir / READY_TO_USE_OUT_NAME
    write_zip(client_entries, client_out)
    write_zip(server_entries, server_out)
    ready_to_use_entries = build_ready_to_use_entries(client_out, server_entries)
    write_zip(ready_to_use_entries, ready_to_use_out)

    for out in (client_out, server_out, ready_to_use_out):
        with zipfile.ZipFile(out, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"{out} failed zip test at {bad}")

    return {
        "client_out": client_out,
        "server_out": server_out,
        "ready_to_use_out": ready_to_use_out,
        "upstream_client": client_zip,
        "server_config": server_config,
        "client_entries": len(client_entries),
        "server_entries": len(server_entries),
        "ready_to_use_entries": len(ready_to_use_entries),
    }


def copy_tree_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    ignore = shutil.ignore_patterns("Server.log", "Server.old.log", "mods.json")
    shutil.copytree(src, dst, ignore=ignore)


def patch_server_config(server_dir: Path, server_port: int | None) -> None:
    config = server_dir / "ServerConfig.toml"
    text = config.read_text(encoding="utf-8")
    if server_port is not None:
        text = re.sub(r"^Port = .*$", f"Port = {server_port}", text, flags=re.MULTILINE)
    text = re.sub(r'^Name = ".*"$', 'Name = "Better Career + CareerMP Test"', text, flags=re.MULTILINE)
    text = re.sub(r'^Description = ".*"$', 'Description = "Better Career + original CareerMP bridge validation"', text, flags=re.MULTILINE)
    config.write_text(text, encoding="utf-8", newline="\n")


def install_test_server(args: argparse.Namespace, artifacts: dict[str, object]) -> Path:
    if args.template_server is None or args.test_server is None:
        raise RuntimeError("--template-server and --test-server are required when installing a validation server.")
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


def run_server_boot_check(server_dir: Path, timeout: int) -> dict[str, object]:
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
    }
    return checks


def validate_outputs(args: argparse.Namespace, artifacts: dict[str, object], test_server: Path, boot: dict[str, object] | None) -> dict[str, object]:
    client_out = artifacts["client_out"]
    server_out = artifacts["server_out"]
    ready_to_use_out = artifacts["ready_to_use_out"]
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
        identity = zf.read("lua/ge/extensions/bcm/identity.lua").decode("utf-8")
        tutorial = zf.read("lua/ge/extensions/bcm/tutorial.lua").decode("utf-8")
        garages = zf.read("lua/ge/extensions/bcm/garages.lua").decode("utf-8")
        per_part_paint = zf.read("lua/ge/extensions/careerMPPerPartPaint.lua").decode("utf-8")
        walk = zf.read("lua/ge/extensions/gameplay/walk.lua").decode("utf-8")
        multimap = zf.read("lua/ge/extensions/bcm/multimap.lua").decode("utf-8")
        multimap_app = zf.read("lua/ge/extensions/bcm/multimapApp.lua").decode("utf-8")
        facilities = zf.read("lua/ge/extensions/overrides/freeroam/facilities.lua").decode("utf-8")

    with zipfile.ZipFile(server_out, "r") as zf:
        server_config = json.loads(zf.read("Resources/Server/CareerMP/config/config.json").decode("utf-8"))

    with zipfile.ZipFile(ready_to_use_out, "r") as zf:
        ready_names = set(zf.namelist())
        ready_client_hash = sha256sum(client_out)
        ready_client_data_hash = hashlib.sha256(zf.read("Resources/Client/CareerMP.zip")).hexdigest()
        ready_server_config = json.loads(zf.read("Resources/Server/CareerMP/config/config.json").decode("utf-8"))

    validation = {
        "missing_required_client_entries": missing,
        "forbidden_career_replacements": forbidden,
        "enabler_uses_better_career": "startBetterCareerCareer" in enabler,
        "enabler_waits_for_bcm_boot": "isBetterCareerBootReady" in enabler,
        "enabler_waits_for_identity_and_tutorial": "bcm_identity" in enabler and "bcm_tutorial" in enabler,
        "enabler_waits_for_property_and_real_estate": "bcm_properties" in enabler
        and "bcm_realEstateApp" in enabler
        and enabler.find('"bcm_properties"') < enabler.find('"bcm_garages"') < enabler.find('"bcm_realEstateApp"'),
        "enabler_reloads_ui_for_beammp": "Reloading UI once so Better Career Vue assets mount after BeamMP mod download" in enabler
        and "reloadUI()" in enabler,
        "enabler_uses_stable_guest_save": "getStableGuestSaveBaseName" in enabler
        and "guestSaveIdentity.json" in enabler
        and "resolveBetterCareerSaveName" in enabler,
        "enabler_does_not_force_career_mp": "career_careerMP" not in enabler,
        "modscript_does_not_load_career_mp": "/career/careerMP" not in modscript,
        "careermp_walk_repositions_existing_unicycle": "repositionUnicycle" in walk,
        "spawn_manager_preserves_beammp_unicycle": "Keeping BeamMP walking vehicle" in spawn_manager,
        "spawn_manager_forces_walking_spawn": "setWalkingMode(true, pos, rot, true)" in spawn_manager,
        "spawn_manager_anchors_existing_beammp_walking_save": "BeamMP/no-player walking state after career load" in spawn_manager,
        "player_driving_respects_careermp_traffic": "Traffic configured by CareerMP" in player_driving,
        "spawn_manager_uses_guarded_tutorial_callback": "extensions.bcm_tutorial" in spawn_manager,
        "spawn_manager_ensures_first_run_modules": "ensure Better Career first-run modules before spawn" in spawn_manager
        or "late first-run modules" in spawn_manager,
        "spawn_manager_direct_tutorial_hook": "directly when it is available" in spawn_manager
        and "bcm_tutorial.onFirstCareerStart" in spawn_manager,
        "identity_retries_beammp_modal": "Identity modal retry for BeamMP late UI" in identity and "M.onUpdate = onUpdate" in identity,
        "identity_autogenerates_for_beammp": "BeamMP identity form did not mount; auto-generating identity" in identity
        and "M.ensureBeamMPFallbackIdentity = ensureBeamMPFallbackIdentity" in identity,
        "garages_defers_starter_until_properties": "deferring starter garage grant" in garages
        and "local record = purchaseBcmGarage(starterId)" in garages,
        "per_part_paint_defers_missing_conditions": "has no partConditions yet; deferring paint sync" in per_part_paint
        and "maxPendingPaintAttempts" in per_part_paint,
        "tutorial_recovers_missed_beammp_start": "Recovering missed first-career tutorial start after BeamMP reload" in tutorial,
        "travel_button_directs_single_destination": "Single reachable destination: travel directly" in facilities,
        "travel_button_closes_activity_prompt": "ChangeState" in facilities and "ChangeState" in multimap_app,
        "multimap_app_keeps_beammp_unpaused": "BeamMP compatibility: never pause" in multimap_app,
        "multimap_treats_walking_as_foot_travel": "gameplay_walk.isWalking()" in multimap and "playerVehId = nil" in multimap,
        "server_autoupdate_disabled": server_config["server"]["autoUpdate"] is False,
        "ready_to_use_has_server_layout": {
            "README_READY_TO_USE.md",
            "Resources/Client/CareerMP.zip",
            "Resources/Server/CareerMP/careerMP.lua",
            "Resources/Server/CareerMP/config/config.json",
            "Resources/Server/CareerMP/versions/client.json",
            "Resources/Server/CareerMP/versions/server.json",
        }.issubset(ready_names),
        "ready_to_use_client_matches_release_client": ready_client_data_hash == ready_client_hash,
        "ready_to_use_autoupdate_disabled": ready_server_config["server"]["autoUpdate"] is False,
        "ready_to_use_uses_public_save_suffix": ready_server_config["client"]["serverSaveSuffix"] == "_BetterCareerMP",
        "boot": boot,
    }
    validation["ok"] = (
        not missing
        and not forbidden
        and validation["enabler_uses_better_career"]
        and validation["enabler_waits_for_bcm_boot"]
        and validation["enabler_waits_for_identity_and_tutorial"]
        and validation["enabler_waits_for_property_and_real_estate"]
        and validation["enabler_reloads_ui_for_beammp"]
        and validation["enabler_uses_stable_guest_save"]
        and validation["enabler_does_not_force_career_mp"]
        and validation["modscript_does_not_load_career_mp"]
        and validation["careermp_walk_repositions_existing_unicycle"]
        and validation["spawn_manager_preserves_beammp_unicycle"]
        and validation["spawn_manager_forces_walking_spawn"]
        and validation["spawn_manager_anchors_existing_beammp_walking_save"]
        and validation["player_driving_respects_careermp_traffic"]
        and validation["spawn_manager_uses_guarded_tutorial_callback"]
        and validation["spawn_manager_ensures_first_run_modules"]
        and validation["spawn_manager_direct_tutorial_hook"]
        and validation["identity_retries_beammp_modal"]
        and validation["identity_autogenerates_for_beammp"]
        and validation["garages_defers_starter_until_properties"]
        and validation["per_part_paint_defers_missing_conditions"]
        and validation["tutorial_recovers_missed_beammp_start"]
        and validation["travel_button_directs_single_destination"]
        and validation["travel_button_closes_activity_prompt"]
        and validation["multimap_app_keeps_beammp_unpaused"]
        and validation["multimap_treats_walking_as_foot_travel"]
        and validation["server_autoupdate_disabled"]
        and validation["ready_to_use_has_server_layout"]
        and validation["ready_to_use_client_matches_release_client"]
        and validation["ready_to_use_autoupdate_disabled"]
        and validation["ready_to_use_uses_public_save_suffix"]
        and (boot is None or (boot["boot_ok"] and boot["career_mp_loaded"] and boot["no_update_attempt"]))
    )
    return validation


def write_report(args: argparse.Namespace, artifacts: dict[str, object], validation: dict[str, object]) -> Path:
    report = {
        "client": {
            "artifact": Path(artifacts["client_out"]).name,
            "sha256": sha256sum(artifacts["client_out"]),
            "size": Path(artifacts["client_out"]).stat().st_size,
            "entries": artifacts["client_entries"],
        },
        "server": {
            "artifact": Path(artifacts["server_out"]).name,
            "sha256": sha256sum(artifacts["server_out"]),
            "size": Path(artifacts["server_out"]).stat().st_size,
            "entries": artifacts["server_entries"],
        },
        "ready_to_use": {
            "artifact": Path(artifacts["ready_to_use_out"]).name,
            "sha256": sha256sum(artifacts["ready_to_use_out"]),
            "size": Path(artifacts["ready_to_use_out"]).stat().st_size,
            "entries": artifacts["ready_to_use_entries"],
        },
        "sources": {
            "better_career": args.better_career.name,
            "server": args.server.name,
            "upstream_client_url": UPSTREAM_CLIENT_URL,
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
    parser.add_argument("--template-server", type=Path)
    parser.add_argument("--test-server", type=Path)
    parser.add_argument("--server-port", type=int)
    parser.add_argument("--skip-test-server", action="store_true")
    parser.add_argument("--boot-check", action="store_true")
    parser.add_argument("--boot-timeout", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifacts = build_artifacts(args)
    test_server = None
    if not args.skip_test_server and (args.template_server is not None or args.test_server is not None):
        test_server = install_test_server(args, artifacts)

    boot = None
    if args.boot_check and test_server is not None:
        boot = run_server_boot_check(test_server, args.boot_timeout)

    validation = validate_outputs(args, artifacts, test_server, boot)
    report_path = write_report(args, artifacts, validation)

    print(json.dumps({
        "client": Path(artifacts["client_out"]).name,
        "server": Path(artifacts["server_out"]).name,
        "ready_to_use": Path(artifacts["ready_to_use_out"]).name,
        "report": report_path.name,
        "validation_ok": validation["ok"],
        "boot": boot,
    }, indent=2))
    return 0 if validation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
