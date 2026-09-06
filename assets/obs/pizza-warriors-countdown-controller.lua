-- Author: Neil Mitchell
-- Last Modified By: Neil Mitchell
obs = obslua

local poll_interval_ms = 250
local refresh_counter = 0
local source_was_active = {}
local last_frontend_scene_name = ""

local function local_asset_url(filename)
    -- OBS supplies script_path() as the directory containing this script.
    -- Encode UTF-8 bytes and URL delimiters while retaining drive/UNC structure.
    local directory = script_path():gsub("\\", "/"):gsub("/+$", "") .. "/"
    local path = (directory .. filename):gsub("([^%w%-%._~/:])", function(character)
        return string.format("%%%02X", string.byte(character))
    end)
    if path:sub(1, 2) == "//" then
        return "file:" .. path
    end
    if path:sub(1, 1) == "/" then
        return "file://" .. path
    end
    return "file:///" .. path
end

local countdown_sources = {
    {
        name = "PW Countdown 30m - Landscape",
        url = local_asset_url("pizza-warriors-countdown-landscape.html"),
    },
    {
        name = "PW Countdown 30m - Vertical",
        url = local_asset_url("pizza-warriors-countdown-vertical.html"),
    },
}

local function refresh_countdown(source_config)
    local source = obs.obs_get_source_by_name(source_config.name)
    if source == nil then
        return
    end

    refresh_counter = refresh_counter + 1
    local settings = obs.obs_source_get_settings(source)
    local cache_buster = string.format("?session=%d-%d", os.time(), refresh_counter)

    obs.obs_data_set_bool(settings, "is_local_file", false)
    obs.obs_data_set_string(settings, "url", source_config.url .. cache_buster)
    obs.obs_source_update(source, settings)

    obs.obs_data_release(settings)
    obs.obs_source_release(source)
end

local function get_frontend_scene_name()
    local scene_source = obs.obs_frontend_get_current_scene()
    if scene_source == nil then
        return ""
    end

    local scene_name = obs.obs_source_get_name(scene_source)
    obs.obs_source_release(scene_source)
    return scene_name or ""
end

local function poll_countdown_sources()
    local frontend_scene_name = get_frontend_scene_name()
    local entered_starting_soon =
        frontend_scene_name == "Starting Soon" and
        last_frontend_scene_name ~= "Starting Soon"

    if entered_starting_soon then
        for _, source_config in ipairs(countdown_sources) do
            refresh_countdown(source_config)
        end
    end

    for _, source_config in ipairs(countdown_sources) do
        local source = obs.obs_get_source_by_name(source_config.name)
        local is_active = false

        if source ~= nil then
            is_active = obs.obs_source_active(source)
            obs.obs_source_release(source)
        end

        if not entered_starting_soon and
            is_active and
            not source_was_active[source_config.name] then
            refresh_countdown(source_config)
        end
        source_was_active[source_config.name] = is_active
    end

    last_frontend_scene_name = frontend_scene_name
end

function script_description()
    return [[
Resets the Pizza Warriors landscape and vertical 30-minute countdowns whenever
the paired Starting Soon scene is entered (with a source-activity fallback).
The controller only reloads local file URLs and does not connect to the
internet or alter audio/output settings.
]]
end

function script_load(settings)
    last_frontend_scene_name = get_frontend_scene_name()
    for _, source_config in ipairs(countdown_sources) do
        source_was_active[source_config.name] = false
    end
    obs.timer_add(poll_countdown_sources, poll_interval_ms)
end

function script_unload()
    obs.timer_remove(poll_countdown_sources)
end
