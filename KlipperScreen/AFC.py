# Armored Turtle Automated Filament Control
#
# Copyright (C) 2024-2025 Armored Turtle
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import math
import os.path
import gi
import pathlib
import re
import time

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango, GLib
from ks_includes.screen_panel import ScreenPanel
from ks_includes.KlippyRest import KlippyRest
from ks_includes.widgets.autogrid import AutoGrid
from ks_includes.widgets.keypad import Keypad
from ks_includes.KlippyGtk import find_widget
from panels import extrude
from datetime import datetime

UNLOADED = "unloaded"
PREP_NOT_LOAD = "prep not load"
LOAD_NOT_PREP = "load not prep"
LOADED = "loaded"
TOOLED = "tooled"
UNLOADING = "tool unloading"
LOADING = "tool loading"
TOOL_LOADED = 'tool loaded'

RESPONSE_LOAD = 1001
RESPONSE_EJECT = 1002
RESPONSE_SET = 1003
RESPONSE_UNLOAD = 1004
RESPONSE_UNSET = 1005

SYSTEM_TYPE_ICONS = {
    "Box_Turtle": "box_turtle_colored_logo.svg",
    "'Box Turtle'": "box_turtle_colored_logo.svg",
    "HTLF": "HTLF.svg",
    "'HTLF'": "HTLF.svg",
    "Night_Owl": "Night_Owl.svg",
    "'NightOwl'": "Night_Owl.svg",
    "AMS": "box_turtle_colored_logo.svg",
    "'AMS'": "box_turtle_colored_logo.svg",
}

def get_widths(widget, name=None):
    pref_min, pref_nat = widget.get_preferred_width()
    alloc = widget.get_allocated_width()
    logging.info(f"{name:20s} | Preferred: {pref_nat:4d}px | Allocated: {alloc:4d}px")

class AFCsystem:
    def __init__(self, current_load, num_units, num_lanes, num_extruders, spoolman, current_toolchange, number_of_toolchanges, extruders, hubs, buffers):
        self.current_load = current_load
        self.num_units = num_units
        self.num_lanes = num_lanes
        self.num_extruders = num_extruders
        self.spoolman = spoolman
        self.current_toolchange = current_toolchange
        self.number_of_toolchanges = number_of_toolchanges
        self.extruders = extruders
        self.hubs = hubs
        self.buffers = buffers

class Extruder:
    def __init__(self, tool_stn, tool_stn_unload, tool_sensor_after_extruder, tool_unload_speed, tool_load_speed, buffer, lane_loaded, tool_start, tool_start_status, tool_end, tool_end_status, lanes):
        self.tool_stn = tool_stn
        self.tool_stn_unload = tool_stn_unload
        self.tool_sensor_after_extruder = tool_sensor_after_extruder
        self.tool_unload_speed = tool_unload_speed
        self.tool_load_speed = tool_load_speed
        self.buffer = buffer
        self.lane_loaded = lane_loaded
        self.tool_start = tool_start
        self.tool_start_status = tool_start_status
        self.tool_end = tool_end
        self.tool_end_status = tool_end_status
        self.lanes = lanes

class Hub:
    def __init__(self, state, cut, cut_cmd, cut_dist, cut_clear, cut_min_length, cut_servo_pass_angle, cut_servo_clip_angle, cut_servo_prep_angle, lanes, afc_bowden_length):
        self.state = state
        self.cut = cut
        self.cut_cmd = cut_cmd
        self.cut_dist = cut_dist
        self.cut_clear = cut_clear
        self.cut_min_length = cut_min_length
        self.cut_servo_pass_angle = cut_servo_pass_angle
        self.cut_servo_clip_angle = cut_servo_clip_angle
        self.cut_servo_prep_angle = cut_servo_prep_angle
        self.lanes = lanes
        self.afc_bowden_length = afc_bowden_length

class Buffer:
    def __init__(self, state, lanes, enabled, belay=None):
        self.state = state
        self.lanes = lanes
        self.enabled = enabled
        self.belay = belay

class AFCunit:
    def __init__(self, name, lanes, system_type, hubs=None):
        self.name = name
        self.lanes = lanes
        self.system_type = system_type
        self.hubs = hubs or []

class AFClane:
    def __init__(self, name, unit, lane_data):
        self.name = name
        self.unit = unit
        self.reinit(lane_data)

    def reinit(self, lane_data):
        self.hub = lane_data.get("hub")
        self.extruder = lane_data.get("extruder")
        self.buffer = lane_data.get("buffer")
        self.buffer_status = lane_data.get("buffer_status")
        self.lane = int(lane_data.get("lane", 0))
        self.map = lane_data.get("map")
        self.load = bool(lane_data.get("load", False))
        self.prep = bool(lane_data.get("prep", False))
        self.tool_loaded = bool(lane_data.get("tool_loaded", False))
        self.loaded_to_hub = bool(lane_data.get("loaded_to_hub", False))
        self.material = lane_data.get("material")
        self.spool_id = int(lane_data.get("spool_id", 0) or 0)
        self.color = lane_data.get("color")
        self.weight = round(float(lane_data.get("weight", 0) or 0))
        self.extruder_temp = lane_data.get("extruder_temp")
        self.runout_lane = lane_data.get("runout_lane")
        self.filament_status = lane_data.get("filament_status")
        self.filament_status_led = lane_data.get("filament_status_led")
        self.lane_status = None

    def __repr__(self):
        return (f"Lane({self.name}, {self.lane}, {self.material}, {self.status}, "
                f"Load={self.load}, Prep={self.prep})")

    def icon(self, width=64, height=64):
        if not hasattr(self, '_icon') or self._icon is None:
            klipperscreendir = pathlib.Path(__file__).parent.resolve().parent
            spool_icon_path = "/path/to/spool.svg"
            if not os.path.isfile(spool_icon_path):
                spool_icon_path = os.path.join(
                    klipperscreendir, "afc_icons", "FilamentReelIcon.svg"
                )
            with open(spool_icon_path, 'r') as f:
                spool_icon_svg = f.read()

            weight = self.weight or 1000
            if self.weight is None:
                weight = 1000
            max_weight = 1000

            # --- SCALE FUNCTION ---
            min_fill_ratio = 0.29
            norm = min(max(weight / max_weight, 0), 1)
            scaled_ratio = min_fill_ratio + (1 - min_fill_ratio) * (norm ** 0.6)

            total_height = 499.8
            scale_y = scaled_ratio
            translate_y = (1 - scale_y) * total_height / 2

            transform_group = f'<g id="scaled_filament" transform="translate(0,{translate_y}) scale(1,{scale_y})">'

            # Remove old clip-paths if any
            spool_icon_svg = re.sub(r'clip-path="url\(#.*?\)"', '', spool_icon_svg)

            # Determine fill color or transparency
            if weight == 0:
                fill_style = 'fill: transparent'
            else:
                color = self.color or "#000000B3"
                fill_style = f'fill: {color}'

            # Replace style in filament_base and apply transform
            spool_icon_svg = re.sub(
                r'(<path[^>]+id="filament_base"[^>]+)style="[^"]+"',
                rf'\1style="{fill_style}"',
                spool_icon_svg
            )
            spool_icon_svg = re.sub(
                r'(<path[^>]+id="filament_base"[^>]*?/?>)',
                rf'{transform_group}\1</g>',
                spool_icon_svg
            )

            # Add xmlns if missing
            if "<svg" in spool_icon_svg and "xmlns=" not in spool_icon_svg:
                spool_icon_svg = spool_icon_svg.replace(
                    "<svg", "<svg xmlns='http://www.w3.org/2000/svg'", 1
                )

            loader = GdkPixbuf.PixbufLoader()
            try:
                loader.write(spool_icon_svg.encode())
                loader.close()
                pixbuf = loader.get_pixbuf()
                self._icon = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            except GLib.Error as e:
                logging.error(f"Failed to load SVG: {e}")
                self._icon = None
        return self._icon




class Panel(ScreenPanel):
    apiClient: KlippyRest
    lane_widgets: dict

    def __init__(self, screen, title):
        title = title or ("AFC Status")
        super().__init__(screen, title)
        self.apiClient = screen.apiclient
        self.lane_widgets = {}
        AFClane.theme_path = screen.theme
        logging.info(f"Theme path: {AFClane.theme_path}")
        klipperscreendir = pathlib.Path(__file__).parent.resolve().parent
        self.image_path = os.path.join(
            klipperscreendir, "afc_icons")
        self.theme_path = os.path.join(
            klipperscreendir, "styles", AFClane.theme_path, "style.css")

        # Control for the update of the UI
        self.update_info = False

        self._pending_lane_grid_updates = {}

        result = self.apiClient.post_request("printer/afc/status", json={})
        if not isinstance(result, dict):
            logging.error(f"API call failed or returned invalid data: {result}")
            # Optionally show a user-friendly error or retry
            # Remove this panel and go back to the previous one
            return

        afc_data = result.get('result', {}).get('status:', {}).get('AFC', {})
        logging.info(f"AFC Data Extracted: {afc_data}")

        self.reset_ui()

        self.afc_units = []
        self.afc_unit_names = []
        self.afc_lane_data = []
        self.afc_lanes = []
        self.afc_unit_map = {}
        self.afc_system = None
        self.current_load = None
        self.spoolman = None
        self.labels = {}
        self.buttons = {}
        self.action_buttons = {}
        self.move_lane = None
        self.distance = 5
        self.last_drop_time = datetime.now()
        self.hub_states = {}  # Track the current state of each hub
        self.selected_lane = None
        self.selected_color = "#000000"
        self.selected_type = None
        self.selected_weight = 0
        self.sensor_poll_id = None  # Store the timeout ID so you can stop it if needed
        self.start_sensor_polling()  # Start periodic sensor polling
        self.virtual_bypass = False
        self.led_state = False  # Track the AFC LED state

        self.oams_manager_available = False
        self.oams_object_lookup = {}
        self._oams_status_cache = {"manager": {"timestamp": 0.0, "status": None}, "oams": {}}
        self.oams_cache_timeout = 0.5

        self.data = self.apiClient.post_request("printer/objects/list", json={})
        sensor_data = self.data.get('result', {}).get('objects', {})
        objects_list = list(sensor_data.keys()) if isinstance(sensor_data, dict) else list(sensor_data or [])
        self._register_oams_objects(objects_list)
        self.filament_sensors = [
            name for name in objects_list
            if isinstance(name, str) and name.startswith("filament_switch_sensor")
        ]

        self.sensors = self.fetch_sensor_data()

        for unit_name, unit_data in afc_data.items():
            if unit_name == "system":
                # Process system data
                self.afc_system = self.process_system_data(unit_data)
                self.current_load = self.afc_system.current_load
                self.spoolman = self.afc_system.spoolman
                self.led_state = unit_data.get("led_state", False)  # Initialize LED state
                logging.info(f"spoolman: {self.spoolman}")
                logging.info(f"LED state: {self.led_state}")
                continue  # Skip the system entry

            if not isinstance(unit_data, dict):
                logging.warning(f"Unexpected unit_data format for {unit_name}: {unit_data}")
                continue

            logging.info(f"Processing unit: {unit_name}")
            unit_lanes = []
            unit_hubs = []

            system_type = unit_data.get("system", {}).get("type", "Unknown")

            for lane_name, lane_data in unit_data.items():
                logging.info(f"Checking lane: {lane_name}")

                if not isinstance(lane_data, dict) or not lane_name.startswith("lane"):
                    logging.info(f"Skipping non-lane entry: {lane_name}")
                    continue

                lane_obj = AFClane(
                    name=lane_name,
                    unit=unit_name,
                    lane_data=lane_data
                )
                lane_obj.status = self.get_lane_status(lane_obj)
                logging.info(f"lane status {lane_obj.status}")

                unit_lanes.append(lane_obj)
                if lane_obj.hub and lane_obj.hub not in unit_hubs:
                    unit_hubs.append(lane_obj.hub)
                self.afc_lane_data.append(lane_obj)
                self.afc_lanes.append(lane_obj.name)

            unit_obj = AFCunit(name=unit_name, lanes=unit_lanes, system_type=system_type, hubs=unit_hubs)
            self.afc_units.append(unit_obj)
            self.afc_unit_names.append(unit_obj.name)

        logging.info(f"Final AFC Lanes: {self.afc_lane_data}")
        logging.info(f"Unit names: {self.afc_unit_names}")
        logging.info(f"lane names: {self.afc_lanes}")

        self.afc_unit_map = {unit.name: unit for unit in self.afc_units}

        self.init_layout()
        self.sensor_layout()
        self.create_spool_layout()
    
    def get_afc_lanes(self):
        """
        Return the list of AFC lanes.
        """
        return self.afc_lanes

    def _register_oams_objects(self, object_names):
        self.oams_manager_available = False
        self.oams_object_lookup.clear()

        for name in object_names:
            if not isinstance(name, str):
                continue

            lower_name = name.lower()
            if lower_name == "oams_manager":
                self.oams_manager_available = True
                continue

            if lower_name.startswith("oams "):
                self._register_oams_object(name)

    def _register_oams_object(self, object_name):
        for variant in self._collect_oams_variants(object_name):
            key = variant.lower()
            if key:
                self.oams_object_lookup[key] = object_name

    def _collect_oams_variants(self, name):
        variants = set()
        if not isinstance(name, str):
            return variants

        trimmed = name.strip().replace("_", " ")
        if not trimmed:
            return variants

        base_values = set()
        base_values.add(trimmed)

        collapsed = trimmed.replace(" ", "")
        if collapsed:
            base_values.add(collapsed)

        lower_trimmed = trimmed.lower()
        suffix = ""

        if lower_trimmed.startswith("oams"):
            suffix = trimmed[4:].strip()
        else:
            parts = trimmed.split()
            if len(parts) > 1 and parts[0].lower() == "oams":
                suffix = " ".join(parts[1:]).strip()

        if suffix:
            base_values.add(suffix)
            collapsed_suffix = suffix.replace(" ", "")
            if collapsed_suffix:
                base_values.add(collapsed_suffix)
                base_values.add(f"oams{collapsed_suffix}")
            base_values.add(f"oams {suffix}")

        for value in base_values:
            if value:
                variants.add(value)
                variants.add(value.lower())
                variants.add(value.upper())

        return {variant for variant in variants if isinstance(variant, str) and variant.strip()}

    def is_ams_lane(self, lane):
        if not lane:
            return False

        unit = self.afc_unit_map.get(lane.unit)
        if not unit:
            return False

        system_type = (unit.system_type or "").strip().upper()
        if "AMS" in system_type:
            return True

        unit_name = (unit.name or "").strip().upper()
        return "AMS" in unit_name

    def _normalize_group_name(self, value):
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        return normalized or None

    def _extract_group_from_key(self, raw_key):
        if not isinstance(raw_key, str):
            return None
        key = raw_key.strip()
        if not key:
            return None

        suffix = key.split()[-1]
        return self._normalize_group_name(suffix)

    def _fetch_object_status(self, object_name, fields=None):
        if not object_name:
            return None

        payload = {"objects": {object_name: fields or []}}
        try:
            result = self.apiClient.post_request("printer/objects/query", json=payload)
        except Exception as err:
            logging.debug(f"Failed to query object {object_name}: {err}")
            return None

        if not isinstance(result, dict):
            return None

        status = result.get("result", {}).get("status", {}).get(object_name)
        return status if isinstance(status, dict) else None

    def _fetch_oams_manager_status(self):
        if not self.oams_manager_available:
            return None

        cache = self._oams_status_cache.get("manager", {})
        now = time.monotonic()
        if cache.get("status") is not None and now - cache.get("timestamp", 0.0) < self.oams_cache_timeout:
            return cache.get("status")

        status = self._fetch_object_status("oams_manager")
        self._oams_status_cache["manager"] = {"timestamp": now, "status": status}
        return status

    def _resolve_oams_object_name(self, raw_name):
        for variant in self._collect_oams_variants(raw_name):
            resolved = self.oams_object_lookup.get(variant.lower())
            if resolved:
                return resolved
        return None

    def _fetch_oams_status(self, object_name):
        resolved_name = self._resolve_oams_object_name(object_name)
        if not resolved_name:
            return None

        cache = self._oams_status_cache.setdefault("oams", {}).setdefault(resolved_name, {"timestamp": 0.0, "status": None})
        now = time.monotonic()
        if cache.get("status") is not None and now - cache.get("timestamp", 0.0) < self.oams_cache_timeout:
            return cache.get("status")

        status = self._fetch_object_status(resolved_name, ["fps_value"])
        cache.update({"timestamp": now, "status": status})
        return status

    def get_lane_fps_value(self, lane):
        if not self.is_ams_lane(lane):
            return None

        lane_map = self._normalize_group_name(getattr(lane, "map", None))
        if not lane_map:
            return None

        manager_status = self._fetch_oams_manager_status()
        if not manager_status:
            return None

        fallback_status = None
        for key, value in manager_status.items():
            if key == "oams" or not isinstance(value, dict):
                continue

            current_group = self._normalize_group_name(value.get("current_group"))
            if current_group == lane_map:
                fallback_status = value
                break

            if not fallback_status:
                key_group = self._extract_group_from_key(key)
                if key_group == lane_map:
                    fallback_status = value

        if not fallback_status:
            return None

        current_oams = fallback_status.get("current_oams")
        if not current_oams:
            return None

        oams_status = self._fetch_oams_status(current_oams)
        if not oams_status:
            return None

        fps_value = oams_status.get("fps_value")
        try:
            value = float(fps_value)
        except (TypeError, ValueError):
            return None

        if not math.isfinite(value):
            return None

        return value

    def format_lane_sensor_label(self, lane):
        fps_value = self.get_lane_fps_value(lane)
        if fps_value is not None:
            return f"FPS: {fps_value:.2f}"

        if lane and lane.buffer and lane.buffer_status:
            return f"Buffer: {lane.buffer} - {lane.buffer_status}"

        return "Buffer: N/A"

    def invalidate_oams_cache(self):
        self._oams_status_cache["manager"] = {"timestamp": 0.0, "status": None}
        self._oams_status_cache["oams"].clear()
        
    def process_update(self, action, data):
        """
        Process the update from the printer.
        """
        if self.update_info == False:
            return

        api_data = self.apiClient.post_request("printer/afc/status", json={})
        if not isinstance(api_data, dict):
            logging.error(f"API call failed or returned invalid data: {api_data}")
            self.update_info = False
            self._screen.show_popup_message(_("AFC panel could not be loaded.\nCheck your printer configuration."))
            self._screen._remove_current_panel()
            self._screen._menu_go_back()  # Go back to main menu or previous panel
            return

        afc_data = api_data.get('result', {}).get('status:', {}).get('AFC', {})
        if afc_data:
            self.update_ui(afc_data)
        else:
            logging.error("No AFC data found in the API response.")
            self.update_info = False
            return

        if action == "notify_gcode_response":
            if "action:cancel" in data or "action:paused" in data:
                self.enable_buttons(True)
            elif self._printer.state == "printing":
                self.enable_buttons(False)
            elif "action:resumed" in data:
                self.enable_buttons(False)
            return
        if action != "notify_status_update":
            return

    def enable_buttons(self, enable):
        if not hasattr(self, "action_buttons") or not self.action_buttons:
            return
        for button in self.action_buttons:
            self.action_buttons[button].set_sensitive(enable)

    def activate(self):
        self.update_info = True
        self.start_sensor_polling()  # Start polling when activated
        if hasattr(self, "screen_stack"):
            self.screen_stack.set_visible_child_name("main_grid")
        self.enable_buttons(self._printer.state in ("ready", "paused"))

    def deactivate(self):
        self.update_info = False
        self.stop_sensor_polling()  # Stop polling when deactivated
        self.enable_buttons(False)

    def start_sensor_polling(self):
        """
        Start polling the sensor data every 10 seconds.
        Delayed start to allow the UI to initialize properly.
        """
        if self.sensor_poll_id is not None:
            GLib.source_remove(self.sensor_poll_id)
            self.sensor_poll_id = None
        # Only start polling if update_info is True
        if self.update_info:
            self.sensor_poll_id = GLib.timeout_add_seconds(10, self.poll_sensors)

    def stop_sensor_polling(self):
        """
        Stop the sensor polling timer.
        """
        if self.sensor_poll_id is not None:
            GLib.source_remove(self.sensor_poll_id)
            self.sensor_poll_id = None

    def poll_sensors(self):
        """
        Fetch and update sensor data. Return True to keep polling.
        """
        if not self.update_info:
            return False  # Stop polling if not active
        if not self.filament_sensors:
            return True  # Keep polling, but nothing to do
        sensor_data = self.fetch_sensor_data()
        self.update_sensors(sensor_data)

        if self.afc_system and self.afc_system.current_load:
            current_lane = next((lane for lane in self.afc_lane_data if lane.name == self.afc_system.current_load), None)
            if self.is_ams_lane(current_lane):
                self.update_system_container()
        return True  # Continue polling

    def process_system_data(self, system_data):
        extruders = {name: Extruder(**data) for name, data in system_data.get("extruders", {}).items()}
        hubs = {name: Hub(**data) for name, data in system_data.get("hubs", {}).items()}
        buffers = {name: Buffer(**data) for name, data in system_data.get("buffers", {}).items()}
        
        return AFCsystem(
            current_load=system_data.get("current_load"),
            num_units=system_data.get("num_units"),
            num_lanes=system_data.get("num_lanes"),
            num_extruders=system_data.get("num_extruders"),
            spoolman=system_data.get("spoolman"),
            current_toolchange=system_data.get("current_toolchange"),
            number_of_toolchanges=system_data.get("number_of_toolchanges"),
            extruders=extruders,
            hubs=hubs,
            buffers=buffers
        )

    def reset_ui(self):
        # Create the main layout grid
        self.screen_stack = Gtk.Stack()
        self.screen_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.screen_stack.set_transition_duration(300)
        self.screen_stack.set_hexpand(False)
        get_widths(self.screen_stack, "screen_stack_reset")

        self.grid = Gtk.Grid(column_homogeneous=True) #AutoGrid()
        self.grid.set_vexpand(True)
        self.grid.set_hexpand(False)

        self.sensor_grid = Gtk.Grid(column_homogeneous=True)
        self.grid.set_vexpand(True)

        self.selector_grid = Gtk.Grid(column_homogeneous=True)
        self.selector_grid.set_vexpand(True)

        self.screen_stack.add_named(self.grid, "main_grid")
        self.screen_stack.add_named(self.sensor_grid, "sensor_grid")
        self.screen_stack.add_named(self.selector_grid, "selector_grid")
        
        self.content.add(self.screen_stack)

    def remove_all_classes(self, widget):
        """
        Remove all CSS classes from the widget's style context.
        """
        style_context = widget.get_style_context()
        for css_class in style_context.list_classes():
            style_context.remove_class(css_class)

    def init_layout(self):
        # Extruder Tools
        extruder_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        extruder_container.set_hexpand(False)
        extruder_tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=False, spacing=5)
        extruder_tools.set_size_request(-1, 20)  # Fixed height
        extruder_tools.set_hexpand(False)
        extruder_tools.set_vexpand(False)

        current_lane = next((lane for lane in self.afc_lane_data if lane.name == self.current_load), None)

        # Create labels
        extruder_label = Gtk.Label(label=f"Extruder: {current_lane.extruder}" if current_lane else "Extruder: N/A")
        buffer_label = Gtk.Label(label=self.format_lane_sensor_label(current_lane))
        loaded_label = Gtk.Label(label=f"Loaded: {self.current_load}" if self.current_load else "Loaded: N/A")

        # Add styling and alignment
        for label, key in zip(
            [extruder_label, buffer_label, loaded_label],
            ["extruder_label", "buffer_label", "loaded_label"]
        ):
            label.get_style_context().add_class(key)
            label.set_hexpand(False)
            label.set_halign(Gtk.Align.FILL)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(30)  # Add this line
            self.labels[key] = label
            extruder_tools.pack_start(label, True, True, 0)


        extruder_container.pack_start(extruder_tools, True, True, 0)
        self.grid.attach(extruder_container, 0, 0, 4, 1)
        extruder_tools.get_style_context().add_class("button_active")

        # Units and Lanes
        self.create_unit_lane_layout()
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(False)
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(300)

        self.control_grid = self.create_controls()
        self.stack.add_named(self.control_grid, "control_grid") 

        self.more_controls = self.create_more_controls()
        self.stack.add_named(self.more_controls, "more_controls")

        self.lane_move_grid = self.create_lane_move_grid()
        self.stack.add_named(self.lane_move_grid, "lane_move_grid")

        self.test_grid = self.create_test_grid()
        self.stack.add_named(self.test_grid, "test_grid")

        self.grid.attach(self.stack, 0, 2, 4, 1)

        logging.info(f"Screen width: {self._screen.width}")

    def create_unit_lane_layout(self):
        unit_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, homogeneous=False, spacing=5)
        unit_box.set_hexpand(False)
        unit_box.set_vexpand(False)  # Ensure unit_box does not expand vertically
        unit_box.set_valign(Gtk.Align.START)
        
        for unit in self.afc_units:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            box.set_hexpand(False)

            # Get the icon filename from the mapping
            icon_filename = SYSTEM_TYPE_ICONS.get(unit.system_type)
            if icon_filename:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        filename=os.path.join(self.image_path, icon_filename),
                        width=45,
                        height=45,
                        preserve_aspect_ratio=True
                    )
                    unit_icon = Gtk.Image.new_from_pixbuf(pixbuf)
                    box.pack_start(unit_icon, False, False, 0)
                except Exception as e:
                    logging.info(f"Could not load image for {unit.system_type}: {e}")
            else:
                logging.warning(f"No icon defined for system type: {unit.system_type}")

            # Create a Gtk.Image from the scaled pixbuf
            unit_name_label = unit.name.replace("_", " ")
            unit_label = Gtk.Label(label=unit_name_label)
            box.pack_start(unit_label, False, False, 0)
            if unit.hubs:
                hub_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                hub_header_text = " | Hubs" if len(unit.hubs) > 1 else " | Hub"
                hub_header = Gtk.Label(label=hub_header_text)
                hub_container.pack_start(hub_header, False, False, 0)

                for hub_name in unit.hubs:
                    hub_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
                    hub_label = Gtk.Label(label=hub_name.replace("_", " "))
                    hub_label.set_halign(Gtk.Align.START)

                    status_dot = Gtk.EventBox()
                    dot = Gtk.Label(label=" ")
                    dot.set_size_request(20, 20)
                    dot.set_valign(Gtk.Align.CENTER)
                    dot.set_halign(Gtk.Align.START)

                    hub_obj = self.afc_system.hubs.get(hub_name) if self.afc_system else None
                    hub_state = hub_obj.state if hub_obj else False
                    logging.info(f"Hub state for {hub_name}: {hub_state}")

                    style_context = dot.get_style_context()
                    style_context.add_class("status-active" if hub_state else "status-empty")

                    status_dot.add(dot)

                    self.labels[f"{hub_name}_status_dot"] = dot
                    self.hub_states[hub_name] = hub_state

                    hub_box.pack_start(status_dot, False, False, 0)
                    hub_box.pack_start(hub_label, False, False, 0)
                    hub_container.pack_start(hub_box, False, False, 0)

                box.pack_start(hub_container, False, False, 0)

            unit_expander = Gtk.Expander()
            unit_expander.set_label_widget(box)
            unit_expander.set_halign(False)

            unit_expander.set_expanded(True)  # Open the first expander by default
            lane_grid = AutoGrid()
            lane_grid.set_vexpand(False)  # Ensure lane_grid does not expand vertically

            # Dynamically calculate the number of lanes per row based on screen width
            logging.info(f"Screen width: {self._screen.width}")
            lane_box_width = 150 + 10  # Lane frame width + margins (adjust as needed)
            lanes_per_row = min(4, max(1, (self._screen.width - 150) // lane_box_width)) # At least one lane per row
            logging.info(f"screen width {self._screen.width}")

            for j, lane in enumerate(unit.lanes):
                # Calculate row and column positions dynamically
                row = j // lanes_per_row  # Each row contains up to 'lanes_per_row' lanes
                col = j % lanes_per_row   # Column index within the row

                lane_frame = Gtk.Frame()
                lane_frame.set_size_request(150, 245)  # Set a fixed width for lane frames
                lane_frame.set_vexpand(False)  # Ensure lane_frame does not expand vertically
                lane_frame.set_hexpand(False)
                lane_frame.set_valign(Gtk.Align.START)

                lane_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                lane_box.set_hexpand(False)
                lane_box.get_style_context().add_class("button_active")

                lane_info_box = self.create_lane_info_box(lane)
                lane_info_box.set_margin_end(5)
                lane_info_box.set_margin_start(5)
                lane_info_box.set_margin_top(5)
                lane_info_box.set_margin_bottom(5)
                lane_info_box.set_valign(Gtk.Align.START)
                lane_box.pack_start(lane_info_box, True, True, 0)

                lane_frame.add(lane_box)
                lane_frame.set_margin_start(5)
                lane_frame.set_margin_end(5)
                lane_frame.set_margin_top(2)
                lane_frame.set_margin_bottom(2)

                # Attach the lane frame to the grid at the calculated position
                lane_grid.attach(lane_frame, col, row, 1, 1)

                if lane.name == self.current_load:
                    lane_frame.get_style_context().add_class("highlighted-lane")

                # Store the lane_box for updating purposes
                self.lane_widgets[f"{lane.name}_frame"] = lane_frame
                self.lane_widgets[lane.name] = lane_box

            unit_expander.add(lane_grid)
            unit_box.pack_start(unit_expander, False, False, 0)

        scroll = self._gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(unit_box)

        self.grid.attach(scroll, 0, 1, 4, 1)  # Attach unit box to grid
        self.grid.show_all()
        GLib.idle_add(self.log_lane_widget_sizes)

        self.set_uniform_frame_height()

    def create_lane_info_box(self, lane):
        lane_info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lane_info_box.set_hexpand(False)
        lane_info_box.set_vexpand(False)
        lane_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        lane_button_box.set_vexpand(False)
        lane_button_box.set_size_request(-1, 30)

        # Create a lane name label
        lane_name = Gtk.Label(label=f"{lane.name}")
        lane_name.set_halign(Gtk.Align.FILL)
        self.labels[f"{lane.name}"] = lane_name
        self.remove_all_classes(lane_name)

        # Set the color of the lane name
        status = self.get_lane_status(lane)
        status_color = self.set_lane_status(lane, status)
        for style in status_color:
            lane_name.get_style_context().add_class(style)

        lane_button_box.pack_start(lane_name, True, True, 0)

        # Replace dropdown with MenuButton for lane mapping
        lane_map_menu_button = self.create_lane_map_menu_button(lane)
        lane_map_menu_button.set_halign(Gtk.Align.FILL)
        lane_map_menu_button.set_size_request(-1, 25)  # Set a fixed height for the menu button
        lane_map_menu_button.get_style_context().add_class("color2")
        lane_button_box.pack_start(lane_map_menu_button, True, True, 0)

        lane_info_box.pack_start(lane_button_box, False, False, 0)

        lane_info_grid = self.create_lane_info_grid(lane, lane_name, status)
        lane_info_box.pack_start(lane_info_grid, False, False, 0)
        
        return lane_info_box

    def create_lane_info_grid(self, lane, lane_name, status):
        lane_info_grid = AutoGrid()
        lane_info_grid.set_row_homogeneous(False)
        lane_info_grid.set_column_homogeneous(True)
        lane_info_grid.set_row_spacing(0)
        lane_info_grid.set_column_spacing(0)
        lane_info_grid.set_hexpand(False)
        lane_info_grid.set_vexpand(False)

        if status == PREP_NOT_LOAD:
            warning_label = Gtk.Label(label="Filament not detected at the extruder", wrap=True)
            warning_label.set_justify(Gtk.Justification.CENTER)
            warning_label.set_valign(Gtk.Align.START)
            lane_info_grid.attach(warning_label, 0, 0, 3, 3)
            lane_name.get_style_context().add_class("status-warning")
        elif status == LOAD_NOT_PREP:
            warning_label = Gtk.Label(label="Lane loaded not prepped", wrap=True)
            warning_label.set_justify(Gtk.Justification.CENTER)
            warning_label.set_valign(Gtk.Align.START)
            lane_info_grid.attach(warning_label, 0, 0, 3, 3)
            lane_name.get_style_context().add_class("status-warning")
        elif status == UNLOADED:
            empty_label = Gtk.Label(label="Lane Empty", wrap=True)
            empty_label.set_justify(Gtk.Justification.CENTER)
            empty_label.set_hexpand(True)
            empty_label.set_valign(Gtk.Align.CENTER)
            empty_label.set_halign(Gtk.Align.FILL)
            empty_label.set_margin_top(45)
            empty_label.set_margin_bottom(45)
            lane_info_grid.attach(empty_label, 2, 2, 3, 3)
            lane_name.get_style_context().add_class("status-lane-empty")
        else:
            overlay = Gtk.Overlay()

            # Icon button is in the background
            icon = Gtk.Image.new_from_pixbuf(lane.icon(width=40, height=70))
            icon_button = Gtk.Button()
            icon_button.set_image(icon)
            icon_button.set_always_show_image(True)
            icon_button.set_halign(Gtk.Align.START)
            icon_button.connect("clicked", self.show_selector_grid, lane)
            icon_button.get_style_context().add_class("no-background")
            overlay.add(icon_button)

            # Runout button in front
            runout_menu_button = self.create_lane_inf_menu_button(lane)
            runout_menu_button.set_halign(Gtk.Align.END)
            runout_menu_button.set_hexpand(True)
            runout_menu_button.get_style_context().add_class("color3")
            overlay.add_overlay(runout_menu_button)

            lane_info_grid.attach(overlay, 0, 0, 2, 1)

            # Material button in b2
            material_button = Gtk.Label(label=f"{lane.material}")
            material_button.set_halign(Gtk.Align.FILL)
            material_button.set_hexpand(True)
            lane_info_grid.attach(material_button, 0, 1, 1, 1)

            # Weight button in b3
            weight_button = Gtk.Label(label=f"{lane.weight}g")
            weight_button.set_halign(Gtk.Align.FILL)
            lane_info_grid.attach(weight_button, 1, 1, 1, 1)

            lane_action_box = self.create_lane_action_box(lane, status)
            lane_info_grid.attach(lane_action_box, 0, 2, 2, 2)

            self.buttons[f"{lane.name}_icon_button"] = icon_button
            self.buttons[f"{lane.name}_runout_button"] = runout_menu_button
            self.labels[f"{lane.name}_material_label"] = material_button
            self.labels[f"{lane.name}_weight_label"] = weight_button

        self.labels[f"{lane.name}_lane_info_grid"] = lane_info_grid

        return lane_info_grid

    def create_test_grid(self):
        """ 
        Create a grid for testing lanes.
        """

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        hbox.set_hexpand(False)
        hbox.set_vexpand(False)
        hbox.set_margin_top(0)
        hbox.set_margin_bottom(5)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)
        hbox.set_halign(Gtk.Align.START)

        exit_button = self._gtk.Button("back", _("back"), "color2")
        exit_button.connect("clicked", self.show_control_grid)
        hbox.pack_start(exit_button, False, False, 5)

        for unit in self.afc_units:
            for lane in unit.lanes:
                lane_button = Gtk.Button(label=f"Test\n{lane.name}")
                lane_button.get_style_context().add_class("color1")
                lane_button.connect("clicked", self.test_lane, lane)
                hbox.pack_start(lane_button, False, False, 5)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)  # horizontal only
        scroller.set_hexpand(True)
        scroller.set_vexpand(False)
        scroller.add(hbox)

        return scroller

    def test_lane(self, widget, lane):
        """
        Test the lane by sending a G-code command.
        """
        if not lane:
            logging.error("No lane selected for testing.")
            return
        logging.info(f"Testing lane: {lane.name}")
        self._screen._send_action(widget, "printer.gcode.script", {"script": f"TEST LANE={lane.name}"})

    def lane_controls(self, widget, lane, status):
        self.selected_lane = lane
        buttons = [
            {"name": _("Go Back"), "response": Gtk.ResponseType.CANCEL, "style": 'dialog-warning'}
        ]
        if self.virtual_bypass == True:
            if status == TOOLED:
                label = Gtk.Label(hexpand=True, vexpand=True, wrap=True)
                label.set_markup(_("Virtual Bypass is enabled, and <b>{}</b> is currently loaded."
                "\nDisable Virtual Bypass to unload <b>{}</b>.").format(lane.name, lane.name))
                self._gtk.Dialog(_("Lane\nControl"), buttons, label, self.control_confirm)
            else:
                buttons.insert(0,{"name": _("Eject"), "response": RESPONSE_EJECT, "style": 'dialog-secondary'})

                label = Gtk.Label(hexpand=True, vexpand=True, wrap=True)
                label.set_markup(_("Virtual Bypass is enabled, select action for <b>{}</b>:").format(lane.name))
                self._gtk.Dialog(_("Lane\nControl"), buttons, label, self.control_confirm)
        else:
            if status == LOADED:
                buttons.insert(0,{"name": _("Load"), "response": RESPONSE_LOAD, "style": 'dialog-primary'})
                buttons.insert(1,{"name": _("Eject"), "response": RESPONSE_EJECT, "style": 'dialog-secondary'})
                buttons.insert(2,{"name": _("Set as current lane"), "response": RESPONSE_SET, "style": 'dialog-warning'})
            elif status == TOOLED:
                buttons.insert(0,{"name": _("Unload"), "response": RESPONSE_UNLOAD, "style": 'dialog-primary'})
                buttons.insert(1,{"name": _("Unset as current lane"), "response": RESPONSE_UNSET, "style": 'dialog-secondary'})

            label = Gtk.Label(hexpand=True, vexpand=True, wrap=True)
            label.set_markup(_("Select action for <b>{}</b>:").format(lane.name))
            self._gtk.Dialog(_("Lane\nControl"), buttons, label, self.control_confirm)

    def control_confirm(self, dialog, response_id):
        self._gtk.remove_dialog(dialog)
        lane = self.selected_lane
        if response_id == RESPONSE_LOAD:
            self._screen._send_action(dialog, "printer.gcode.script", {"script": f"CHANGE_TOOL LANE={lane.name}"})
        elif response_id == RESPONSE_UNLOAD:
            self._screen._send_action(dialog, "printer.gcode.script", {"script": "TOOL_UNLOAD"})
        elif response_id == RESPONSE_EJECT:
            self._screen._send_action(dialog, "printer.gcode.script", {"script": f"LANE_UNLOAD LANE={lane.name}"})
        elif response_id == RESPONSE_SET:
            self._screen._send_action(dialog, "printer.gcode.script", {"script": f"SET_LANE_LOADED LANE={lane.name}"})
        elif response_id == RESPONSE_UNSET:
            self._screen._send_action(dialog, "printer.gcode.script", {"script": "UNSET_LANE_LOADED"})
        elif response_id == Gtk.ResponseType.CANCEL:
            dialog.destroy()

    def create_lane_action_box(self, lane, status):
        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, homogeneous=True, spacing=1)
        action_box.set_hexpand(False)
        action_box.set_vexpand(False)  # Ensure unit_box does not expand vertically

        self.action_buttons[f'{lane.name}_controls'] = Gtk.Button()
        label = Gtk.Label(label=_("Controls"))
        label.set_halign(Gtk.Align.FILL)
        label.set_valign(Gtk.Align.FILL)
        self.action_buttons[f'{lane.name}_controls'].add(label)
        self.action_buttons[f'{lane.name}_controls'].set_halign(Gtk.Align.FILL)
        self.action_buttons[f'{lane.name}_controls'].set_hexpand(True)
        self.action_buttons[f'{lane.name}_controls'].get_style_context().add_class("color4")
        self.action_buttons[f'{lane.name}_controls'].get_style_context().add_class("control-button")
        self.action_buttons[f'{lane.name}_controls'].connect("clicked", self.lane_controls, lane, status)
        action_box.pack_start(self.action_buttons[f'{lane.name}_controls'], True, True, 0)

        return action_box

    def calculate_max_frame_height(self):
        max_height = 250
        for lane_name, lane_box in self.lane_widgets.items():
            # Skip any keys ending with "_frame"
            if lane_name.endswith("_frame"):
                continue

            # Get the parent frame of the lane_box
            lane_frame = lane_box.get_parent()
            if lane_frame:
                _, natural_height = lane_frame.get_preferred_height()
                max_height = max(max_height, natural_height)
                logging.info(f"Lane {lane_name} natural height: {natural_height}, max height so far: {max_height}")

        logging.info(f"Calculated maximum frame height: {max_height}")
        return max_height

    def set_uniform_frame_height(self):
        max_height = self.calculate_max_frame_height()
        for lane_name, lane_box in self.lane_widgets.items():
            if lane_name.endswith("_frame"):
                continue

            lane_frame = lane_box.get_parent()
            if lane_frame:
                lane_frame.set_size_request(-1, max_height)
                lane_frame.queue_resize()

    ################
    # Controls     #
    ################

    def create_controls(self):
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        controls_box.set_hexpand(False)
        controls_box.set_vexpand(False)

        button_grid = AutoGrid()
        button_grid.set_column_homogeneous(False)
        button_grid.set_halign(Gtk.Align.START)

        move_button = self._gtk.Button("filament", _("Lane Move"), "color1")
        move_button.connect("clicked", self.show_lane_move_grid)
        self.action_buttons['lane_move'] = move_button
        button_grid.attach(move_button, 2, 0, 1, 1)

        # Add the filament sensors button
        sensors_button = self._gtk.Button("info", _("Sensors"), "color4")
        sensors_button.connect("clicked", self.show_sensor_grid)
        button_grid.attach(sensors_button, 3, 0, 1, 1)

        macro_button = self._gtk.Button("custom-script", _("Macros"), "color3")
        macro_button.set_halign(Gtk.Align.START)
        macro_button.connect("clicked", self.afc_macros)
        self.action_buttons['afc_macros'] = macro_button
        button_grid.attach(macro_button, 4, 0, 1, 1)

        # Add a button to navigate to more controls
        more_controls_button = self._gtk.Button("increase", _("More"), "color2")
        more_controls_button.connect("clicked", self.show_more_controls)
        button_grid.attach(more_controls_button, 7, 0, 1, 1)

        virtual_bypass_toggle = self.create_virtual_bypass_toggle()
        button_grid.attach(virtual_bypass_toggle, 5, 0, 1, 1)

        # Create a single AFC LED toggle button
        afc_led_toggle = self.create_afc_led_toggle()
        button_grid.attach(afc_led_toggle, 6, 0, 1, 1)

        controls_box.pack_start(button_grid, False, False, 0)

        toolchange_label = Gtk.Label(label="Toolchanges:")
        toolchange_label.set_halign(Gtk.Align.END)
        controls_box.pack_start(toolchange_label, False, False, 0)

        toolchange_combined_label = Gtk.Label(label="0/0")
        toolchange_combined_label.get_style_context().add_class("toolchange_combined_label")
        self.labels["toolchange_combined_label"] = toolchange_combined_label  # Store reference for updates
        controls_box.pack_start(toolchange_combined_label, False, False, 10)

        alignment = Gtk.Alignment.new(0.5, 1.0, 1.0, 0.0)
        alignment.add(controls_box)

        return alignment

    def create_virtual_bypass_toggle(self):
        """
        Create the AFC Virtual Bypass toggle button.
        This button allows the user to enable or disable the virtual bypass feature.
        """

        sensor_name = "filament_switch_sensor virtual_bypass"
        vb_status = self.sensors.get(sensor_name, {}).get("filament_detected", False)
        logging.info(f"Virtual Bypass status: {vb_status}")
        self.virtual_bypass = vb_status  # Store the initial state

        # Create the AFC Virtual Bypass button
        self.action_buttons[f'afc_vb_button'] = Gtk.Button()

        # Create and configure the multiline label
        vb_label = Gtk.Label(label="Virtual\nBypass")
        vb_label.set_halign(Gtk.Align.CENTER)
        vb_label.set_valign(Gtk.Align.CENTER)
        vb_label.set_line_wrap(True)
        vb_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        vb_label.set_ellipsize(Pango.EllipsizeMode.NONE)

        self.action_buttons[f'afc_vb_button'].add(vb_label)
        self.action_buttons[f'afc_vb_button'].show_all()  # Important to make sure the label appears

        # Set initial style
        style = self.action_buttons[f'afc_vb_button'].get_style_context()
        style.add_class("color2")
        style.remove_class("vb_active")
        style.remove_class("vb_inactive")
        if vb_status:
            style.add_class("vb_active")
        else:
            style.add_class("vb_inactive")

        # Store reference for updates
        self.virtual_bypass_button = self.action_buttons[f'afc_vb_button']

        # Connect the button to the toggle handler
        self.action_buttons[f'afc_vb_button'].connect("clicked", self.on_afc_vb_toggled)

        return self.action_buttons[f'afc_vb_button']


    def on_afc_vb_toggled(self, button):
        """
        Handle the toggle of the AFC virtual bypass button.
        """
        # Toggle the state
        new_state = not self.virtual_bypass
        self.virtual_bypass = new_state

        # Update the button style
        style = button.get_style_context()
        style.remove_class("vb_active")
        style.remove_class("vb_inactive")
        if new_state:
            style.add_class("vb_active")
            logging.info("AFC Virtual Bypass enabled")
            self._screen.show_popup_message(_("Virtual Bypass enabled"), 1)
            self._screen._ws.klippy.gcode_script("SET_FILAMENT_SENSOR SENSOR=virtual_bypass ENABLE=1")
        else:
            style.add_class("vb_inactive")
            logging.info("AFC Virtual Bypass disabled")
            self._screen.show_popup_message(_("Virtual Bypass Disabled"), 1)
            self._screen._ws.klippy.gcode_script("SET_FILAMENT_SENSOR SENSOR=virtual_bypass ENABLE=0")

    def update_virtual_bypass_toggle(self, new_status):
        """
        Update the Virtual Bypass toggle button if its state has changed.
        """
        if hasattr(self, "virtual_bypass_button"):
            style = self.virtual_bypass_button.get_style_context()
            style.remove_class("vb_active")
            style.remove_class("vb_inactive")
            if new_status:
                style.add_class("vb_active")
            else:
                style.add_class("vb_inactive")
            self.virtual_bypass = bool(new_status)

    def show_lane_move_grid(self, button):
        """
        Switch to the lane move grid.
        """
        self.stack.set_visible_child_name("lane_move_grid")

    def show_control_grid(self, button):
        """
        Switch back to the control grid.
        """
        self.stack.set_visible_child_name("control_grid")

    def show_more_controls(self, button):
        """
        Switch to the 'More Controls' section.
        """
        self.stack.set_visible_child_name("more_controls")

    def show_main_grid(self, button):
        """
        Switch to the lane move grid.
        """
        self.screen_stack.set_visible_child_name("main_grid")

    def show_sensor_grid(self, button):
        """
        Switch to the lane move grid.
        """
        self.on_refresh_clicked(button)
        self.screen_stack.set_visible_child_name("sensor_grid")

    def show_selector_grid(self, button, lane):
        """
        Switch to the selector grid and populate input fields with the lane's information.
        """
        if self.spoolman is not None:
            self._screen.show_popup_message(_("Spoolman not currently supported, manual spool set available"),2)

        logging.info(f"Switching to selector grid for lane: {lane.name}")
        self.selected_lane = lane  # Store the selected lane

        self.labels["title_label"].set_label(f"Change Spool {lane.name}")

        # Populate input fields with lane information
        if lane.color:
            rgba = Gdk.RGBA()
            rgba.parse(lane.color)
            r = int(rgba.red * 255)
            g = int(rgba.green * 255)
            b = int(rgba.blue * 255)
            self.r_input.set_text(str(r))
            self.g_input.set_text(str(g))
            self.b_input.set_text(str(b))
            self.hex_input.set_text(f"#{r:02X}{g:02X}{b:02X}")
            self.color_button.set_rgba(rgba)  # Update the color button
        else:
            # Default color if no color is set
            self.r_input.set_text("255")
            self.g_input.set_text("0")
            self.b_input.set_text("0")
            self.hex_input.set_text("#FF0000")
            self.color_button.set_rgba(Gdk.RGBA(1, 0, 0, 1))  # Default to red

        if lane.material:
            self.labels["type_input"].set_text(lane.material)
        else:
            self.labels["type_input"].set_text("")

        if lane.weight:
            self.labels["weight_input"].set_text(str(lane.weight))
        else:
            self.labels["weight_input"].set_text("")

        self.screen_stack.set_visible_child_name("selector_grid")
        self._screen.remove_keyboard()

    def create_lane_move_grid(self):
        """
        Create the lane move grid with dropdown, distance grid, move button, and exit button.
        """
        vbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        vbox.set_hexpand(False)
        vbox.set_vexpand(False)
        vbox.set_margin_top(0)
        vbox.set_margin_bottom(5)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)

        exit_button = self._gtk.Button("back", _("back"), "color2")
        exit_button.connect("clicked", self.show_control_grid)
        vbox.pack_start(exit_button, False, False, 5)

        # Create the dropdown for lane selection
        lane_dropdown = self.create_lane_dropdown()
        lane_dropdown.set_valign(Gtk.Align.CENTER)
        vbox.pack_start(lane_dropdown, False, False, 0)

        neg_move_button = self._gtk.Button("decrease", _("Move"), "color1")
        neg_move_button.connect("clicked", self.on_neg_move_button_clicked)
        vbox.pack_start(neg_move_button, False, False, 5)

        # Create the distance grid
        distbox = self.create_distance_grid()
        vbox.pack_start(distbox, True, True, 0)

        # Add the move button
        move_button = self._gtk.Button("increase", _("Move"), "color3")
        move_button.connect("clicked", self.on_move_button_clicked)
        vbox.pack_start(move_button, False, False, 5)

        return vbox

    def update_toolchange_combined_label(self):
        """
        Update the combined toolchange/toolchanges label in the UI.
        """
        toolchange_combined_label = self.labels.get("toolchange_combined_label")
        if toolchange_combined_label:
            toolchange_combined_label.set_label(
                f"{self.afc_system.current_toolchange}/{self.afc_system.number_of_toolchanges}")

    #################
    # More Controls #
    #################

    def create_more_controls(self):
        """
        Create the 'More Controls' section with additional controls like the AFC LED switch.
        """
        more_controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        more_controls_box.set_hexpand(False)
        more_controls_box.set_vexpand(False)

        # Add a back button to return to the main controls
        exit_button = self._gtk.Button("back", _("back"), "color2")
        exit_button.set_halign(Gtk.Align.START)
        exit_button.connect("clicked", self.show_control_grid)
        more_controls_box.pack_start(exit_button, False, False, 5)

        calibration_button = self._gtk.Button("fine-tune", _("Calibration"), "color1")
        calibration_button.set_halign(Gtk.Align.START)
        calibration_button.connect("clicked", self.on_calibration_clicked)
        self.action_buttons['calibration'] = calibration_button
        more_controls_box.pack_start(calibration_button, False, False, 5)

        test_button = self._gtk.Button("retract", _("Test"), "color1")
        test_button.set_halign(Gtk.Align.START)
        test_button.connect("clicked", self.on_test_clicked)
        self.action_buttons['test'] = test_button
        more_controls_box.pack_start(test_button, False, False, 5)

        return more_controls_box

    def on_calibration_clicked(self, switch):
        self._screen._send_action(switch, "printer.gcode.script", {"script": "AFC_CALIBRATION"})
        logging.info("AFC Calibration button clicked")

    def on_test_clicked(self, switch):
        self.stack.set_visible_child_name("test_grid")
        logging.info("AFC Test button clicked")

    def afc_macros(self, widget):
        name = "afc_macros"
        disname = self._screen._config.get_menu_name("afc", name)
        menuitems = self._screen._config.get_menu_items("afc", name)
        self._screen.show_panel("menu", disname, panel_name=name, items=menuitems)

    def on_afc_led_on(self, button):
        """
        Handle the AFC LED switch state and update its style.
        """
        self._screen._send_action(button, "printer.gcode.script", {"script": "TURN_ON_AFC_LED"})

    def on_afc_led_off(self, button):
        """
        Handle the AFC LED switch state and update its style.
        """
        self._screen._send_action(button, "printer.gcode.script", {"script": "TURN_OFF_AFC_LED"})

    def create_afc_led_toggle(self):
        """
        Create the AFC LED toggle button.
        This button allows the user to enable or disable the AFC LED.
        """
        # Create the AFC LED button
        self.action_buttons['afc_led_button'] = Gtk.Button()

        # Create and configure the multiline label
        led_label = Gtk.Label(label="AFC\nLED")
        led_label.set_halign(Gtk.Align.CENTER)
        led_label.set_valign(Gtk.Align.CENTER)
        led_label.set_line_wrap(True)
        led_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        led_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        led_label.set_margin_start(4)
        led_label.set_margin_end(4)

        self.action_buttons['afc_led_button'].add(led_label)
        self.action_buttons['afc_led_button'].show_all()

        # Set initial style based on current LED state
        style = self.action_buttons['afc_led_button'].get_style_context()
        style.add_class("color2")
        style.remove_class("vb_active")
        style.remove_class("vb_inactive")
        if self.led_state:
            style.add_class("vb_active")
        else:
            style.add_class("vb_inactive")

        # Store reference for updates
        self.afc_led_button = self.action_buttons['afc_led_button']

        # Connect the button to the toggle handler
        self.action_buttons['afc_led_button'].connect("clicked", self.on_afc_led_toggled)

        return self.action_buttons['afc_led_button']

    def on_afc_led_toggled(self, button):
        """
        Handle the toggle of the AFC LED button.
        """
        # Toggle the state
        new_state = not self.led_state
        self.led_state = new_state

        # Update the button style
        style = button.get_style_context()
        style.remove_class("vb_active")
        style.remove_class("vb_inactive")
        if new_state:
            style.add_class("vb_active")
            logging.info("AFC LED enabled")
            self._screen.show_popup_message(_("AFC LED enabled"), 1)
            self._screen._send_action(button, "printer.gcode.script", {"script": "TURN_ON_AFC_LED"})
        else:
            style.add_class("vb_inactive")
            logging.info("AFC LED disabled")
            self._screen.show_popup_message(_("AFC LED disabled"), 1)
            self._screen._send_action(button, "printer.gcode.script", {"script": "TURN_OFF_AFC_LED"})

    def update_afc_led_toggle(self, new_status):
        """
        Update the AFC LED toggle button if its state has changed.
        """
        if hasattr(self, "afc_led_button"):
            style = self.afc_led_button.get_style_context()
            style.remove_class("vb_active")
            style.remove_class("vb_inactive")
            if new_status:
                style.add_class("vb_active")
            else:
                style.add_class("vb_inactive")
            self.led_state = bool(new_status)

    def create_lane_map_menu_button(self, lane):
        """
        Creates a MenuButton for lane mapping, displaying T values instead of lane names.

        :param lane: The current lane object.
        :return: A Gtk.MenuButton widget.
        """
        options = {
            f"T{lane_num - 1}": lane_name
            for lane_num, lane_name in enumerate(self.afc_lanes, start=1)
        }
        # options = {"T1": "T1", "T2": "T2", "T3": "T3", "T4": "T4", "T5": "T5", "T6": "T6",
        # "T11": "T11", "T21": "T21", "T31": "T31", "T41": "T41", "T51": "T51", "T61": "T61",
        # "T12": "T12", "T22": "T22", "T32": "T32", "T42": "T42", "T52": "T52", "T62": "T62"}

        logging.info(f"Options passed to menu button: {options}")
        logging.info(f"Initial selection (lane.map): {lane.map}")

        initial_selection = None
        for t_value, lane_name in options.items():
            if t_value == lane.map:
                initial_selection = t_value
                break

        if initial_selection is None:
            logging.warning(f"Initial map value '{lane.map}' not found in options. Defaulting to the first.")
            initial_selection = list(options.keys())[0]

        # Create the menu button
        label = Gtk.Label(label=lane.map)
        menu_button = Gtk.MenuButton()
        menu_button.add(label)
        menu_button.set_halign(Gtk.Align.END)
        menu_button.set_vexpand(False)

        # Create the popover
        popover = Gtk.Popover()

        # Add a scrolled window to the popover
        scrolled_window = self._gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_min_content_height(450)  # adjust to your needs
        scrolled_window.set_max_content_height(600)  # limit how tall the popover grows
        scrolled_window.set_min_content_width(600)  # adjust to your needs

        # Container for the scrollable content
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=5)
        scrolled_window.add(box)

        # Add buttons to the box
        for t_value in options:
            item = Gtk.ModelButton(label=t_value)
            item.get_style_context().add_class("scroll_button")
            item.set_halign(Gtk.Align.START)
            item.get_style_context().add_class("large-button")

            def on_select(_, value=t_value):
                logging.info(f"MenuButton selection changed: {value}")
                self.on_lane_map_changed(menu_button, value, lane, label)

            item.connect("clicked", on_select)
            box.pack_start(item, True, True, 2)

        box.show_all()
        scrolled_window.show_all()
        popover.add(scrolled_window)
        menu_button.set_popover(popover)

        self.labels[f"{lane.name}_map_menu_button"] = menu_button
        return menu_button


    def create_lane_inf_menu_button(self, lane):
        """
        Creates a MenuButton for lane INF selection, displaying lane names.

        :param lane: The current lane object.
        :return: A Gtk.MenuButton widget.
        """
        # Use lane names as options, excluding the current lane's name and adding "None"
        options = ["NONE"] + [lane_name for lane_name in self.afc_lanes if lane_name != lane.name]

        logging.info(f"Options passed to menu button: {options}")
        logging.info(f"Initial selection (lane.inf): {lane.runout_lane}")

        # Validate current lane.inf
        initial_selection = lane.runout_lane if lane.runout_lane in options else "NONE"
        if lane.runout_lane not in options:
            logging.warning(f"Initial INF value '{lane.runout_lane}' not found in options. Defaulting to '{initial_selection}'")

        # Create the menu button
        label = Gtk.Label(label=f"{initial_selection} ∞")
        menu_button = Gtk.MenuButton()
        menu_button.add(label)
        menu_button.set_halign(Gtk.Align.END)
        menu_button.set_vexpand(False)

        # Create the popover menu
        popover = Gtk.Popover()

        # Add a scrolled window to the popover
        scrolled_window = self._gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_min_content_height(450)  # adjust to your needs
        scrolled_window.set_max_content_height(600)  # limit how tall the popover grows
        scrolled_window.set_min_content_width(600)  # adjust to your needs

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        scrolled_window.add(box)

        # Create buttons for each option
        for lane_name in options:
            item = Gtk.ModelButton(label=lane_name)
            item.get_style_context().add_class("scroll_button")
            item.set_name("lane-inf-item")
            item.set_halign(Gtk.Align.FILL)
            item.get_style_context().add_class("large-button")

            def on_select(_, value=lane_name):
                logging.info(f"MenuButton selection changed: {value} ∞")
                self.on_lane_inf_changed(menu_button, value, lane, label)

            item.connect("clicked", on_select)
            box.pack_start(item, True, True, 0)

        box.show_all()
        scrolled_window.show_all()
        popover.add(scrolled_window)
        menu_button.set_popover(popover)

        # Store reference
        self.labels[f"{lane.name}_inf_menu_button"] = menu_button
        return menu_button

    ##################
    #    Updating    #
    ##################

    def update_ui(self, afc_data):
        try:
            if not afc_data or not isinstance(afc_data, dict):
                logging.error("Invalid AFC data received")
                return

            # Update AFCsystem attributes
            system_data = afc_data.get("system", {})
            if system_data:
                self.update_afc_system(system_data)
                # Update LED state if it has changed
                new_led_state = system_data.get("led_state", self.led_state)
                if self.led_state != new_led_state:
                    logging.info(f"LED state changed: {self.led_state} → {new_led_state}")
                    self.update_afc_led_toggle(new_led_state)

            # Update hub statuses
            hubs_data = system_data.get("hubs", {})  # Corrected path to hubs data
            if not hubs_data:
                logging.warning("No hub data found in the AFC system data")
            else:
                for hub_name, hub_data in hubs_data.items():
                    hub_state = hub_data.get("state", False)  # Default to False if state is not provided
                    self.update_hub_status(hub_name, hub_state)

            for unit in self.afc_units:
                for lane in unit.lanes:
                    lane_data = afc_data.get(unit.name, {}).get(lane.name)
                    if not lane_data:
                        logging.warning(f"No data found for lane: {lane.name}")
                        continue

                    lane_status = self.get_lane_status_from_data(lane_data)
                    lane_name = self.labels.get(f"{lane.name}")
                    old_buffer = getattr(lane, "buffer", None)
                    old_buffer_status = getattr(lane, "buffer_status", None)

                    lane.buffer = lane_data.get("buffer", lane.buffer)
                    lane.buffer_status = lane_data.get("buffer_status", lane.buffer_status)

                    if lane.status != lane_status:
                        self.handle_lane_status_update(lane, lane_status)

                    new_map = lane_data.get("map", lane.map)
                    if lane.map != new_map:
                        logging.info(f"Updating mapping for {lane.name}: {lane.map} → {new_map}")
                        lane.map = new_map
                        self.update_lane_map(lane)
                        if self.afc_system and lane.name == self.afc_system.current_load:
                            self.invalidate_oams_cache()
                            self.update_system_container()

                    if lane.runout_lane != lane_data.get("runout_lane", lane.runout_lane):
                        lane.runout_lane = lane_data.get("runout_lane", lane.runout_lane)
                        self.update_lane_runout(lane)

                    if lane.material != lane_data.get("material", lane.material):
                        lane.material = lane_data.get("material", lane.material)
                        self.update_lane_material(lane)

                    new_weight = round(float(lane_data.get("weight", lane.weight) or 0))
                    if lane.weight != new_weight:
                        lane.weight = new_weight
                        self.update_lane_weight(lane)

                    if lane.color != lane_data.get("color", lane.color):
                        lane.color = lane_data.get("color", lane.color)
                        self.update_lane_color(lane)

                    if lane.load != bool(lane_data.get("load", lane.load)):
                        lane.load = bool(lane_data.get("load", lane.load))
                        self.update_lane_load(lane)

                    if lane.name == (self.afc_system.current_load if self.afc_system else None):
                        if lane.buffer != old_buffer or lane.buffer_status != old_buffer_status:
                            self.update_system_container()

        except Exception as e:
            logging.error(f"Failed to update UI: {e}")

    def update_hub_status(self, hub_name, hub_state):
        """
        Update the status dot style based on the hub's state, only if the state changes.

        :param hub_name: The name of the hub.
        :param hub_state: The current state of the hub (True for active, False for empty).
        """
        # Check if the state has changed
        if self.hub_states.get(hub_name) == hub_state:
            # logging.info(f"No change in state for hub: {hub_name} (state: {hub_state})")
            return  # No update needed

        # Update the stored state
        self.hub_states[hub_name] = hub_state

        # Get the status dot widget
        status_dot = self.labels.get(f"{hub_name}_status_dot")
        if not status_dot:
            # logging.warning(f"Status dot not found for hub: {hub_name}")
            return

        # Get the style context of the status dot
        style_context = status_dot.get_style_context()

        # Remove all existing status-related classes
        style_context.remove_class("status-active")
        style_context.remove_class("status-empty")

        # Add the appropriate class based on the hub state
        if hub_state:
            style_context.add_class("status-active")  # Green for active
            # logging.info(f"Hub {hub_name} state updated to ACTIVE")
        else:
            style_context.add_class("status-empty")  # Red for empty
            # logging.info(f"Hub {hub_name} state updated to EMPTY")

    def update_afc_system(self, system_data):
        """
        Update the AFCsystem attributes and check for changes in current_load,
        current_toolchange, and number_of_toolchanges.
        """
        if not self.afc_system:
            logging.warning("AFCsystem is not initialized.")
            return

        # Check and update current_load
        new_current_load = system_data.get("current_load", self.afc_system.current_load)
        if self.afc_system.current_load != new_current_load:
            logging.info(f"Current load changed: {self.afc_system.current_load} → {new_current_load}")
            self.invalidate_oams_cache()
            self.afc_system.current_load = new_current_load
            self.update_system_container()

        # Check and update current_toolchange
        new_toolchange = system_data.get("current_toolchange", self.afc_system.current_toolchange)
        new_toolchange_count = system_data.get("number_of_toolchanges", self.afc_system.number_of_toolchanges)

        if (self.afc_system.current_toolchange != new_toolchange or
                self.afc_system.number_of_toolchanges != new_toolchange_count):
            logging.info(f"Toolchange updated: {self.afc_system.current_toolchange}/{self.afc_system.number_of_toolchanges} → {new_toolchange}/{new_toolchange_count}")
            self.afc_system.current_toolchange = new_toolchange
            self.afc_system.number_of_toolchanges = new_toolchange_count
            self.update_toolchange_combined_label()

    def handle_lane_status_update(self, lane, lane_status):
        logging.info(f"Handling lane status update for {lane.name}: {lane.status} → {lane_status}")

        UNREADY_STATUSES = {UNLOADED, PREP_NOT_LOAD, LOAD_NOT_PREP}
        READY_STATUSES = {LOADED, TOOLED, LOADING, UNLOADING}

        def status_category(status):
            if status in UNREADY_STATUSES:
                return "unready"
            elif status in READY_STATUSES:
                return "ready"
            return "other"

        old_status = lane.status  # Save the previous status
        logging.info(f"Old status for {lane.name}: {old_status}, New status: {lane_status}")

        frame_context = self.lane_widgets[f"{lane.name}_frame"].get_style_context()
        if old_status in {UNLOADING, TOOLED} and lane_status in {LOADED, "null"}:
            frame_context.remove_class("highlighted-lane")
        elif lane_status in {LOADING, TOOLED, TOOL_LOADED}:
            frame_context.add_class("highlighted-lane")
        

        old_category = status_category(old_status)
        new_category = status_category(lane_status)

        # Update the lane's status before rebuilding the grid
        lane.status = lane_status

        # Determine if the UI needs to be refreshed
        should_update = (
            old_category != new_category  # Changed category (e.g., ready ↔ unready)
            or (old_status != lane_status and old_category == "unready")  # Changed within unready
        )

        if old_status != lane_status:
            logging.info(f"Updating UI grid for {lane.name} due to status change: {lane_status}")
            self.replace_lane_info_grid(lane, self.labels.get(f"{lane.name}"), lane_status)

        # Update the lane's status in the UI
        self.update_lane_status(lane, lane_status)

    def update_lane_status(self, lane, status):
        """
        Update the status part of the UI, including the lane's color and status.
        """
        lane_box = self.lane_widgets.get(lane.name)
        logging.info(f"Updating lane status for {lane.name}: {status}")
        if not lane_box:
            logging.info(f"Lane box not found for lane: {lane.name}")
            return

        # Get the lane menu button widget
        lane_name = self.labels.get(f"{lane.name}")
        if not lane_name:
            logging.info(f"Lane menu button not found for lane: {lane.name}")
            return

        # Determine the new status color class
        status_color = self.set_lane_status(lane, status)

        # Update the style context of the lane menu button
        style_context = lane_name.get_style_context()

        # Remove all existing status-related classes
        style_context.remove_class("status-tooled")
        style_context.remove_class("status-loaded")
        style_context.remove_class("status-warning")
        style_context.remove_class("status-lane-empty")

        # Add the new status class
        for style in status_color:
            style_context.add_class(style)
            # logging.info(f"Updated lane {lane.name} to status: {status} with color class: {status_color}")

        # Force a redraw of the lane box
        lane_box.show()

    def update_lane_map(self, lane):
        """
        Update the map part of the UI, refreshing the dropdown to reflect the new mapping.
        """
        lane_map_widget = self.labels.get(f"{lane.name}_map_menu_button")
        if lane_map_widget:
            label = lane_map_widget.get_child()
            if label:
                label.set_text(lane.map)

    def update_lane_runout(self, lane):
        # Update the runout lane part of the UI
        runout_button = self.buttons.get(f"{lane.name}_runout_button")
        if runout_button:
            runout_button.set_label(f"{lane.runout_lane} ∞")

    def update_lane_material(self, lane):
        # Update the material part of the UI
        material_label = self.labels.get(f"{lane.name}_material_label")
        if material_label:
            material_label.set_label(f"{lane.material}")

    def update_lane_weight(self, lane):
        # Update the weight part of the UI
        weight_label = self.labels.get(f"{lane.name}_weight_label")
        if weight_label:
            weight_label.set_label(f"{lane.weight}g")
            self.update_lane_color(lane)  # Update the lane icon based on weight

    def update_lane_color(self, lane):
        lane._icon = None  # Clear the cached icon
        icon_button = self.buttons.get(f"{lane.name}_icon_button")
        if icon_button:
            icon = Gtk.Image.new_from_pixbuf(lane.icon(width=40,height=70))
            icon_button.set_image(icon)
            logging.info(f"replacing icon {icon}")
            icon_button.show_all()

    def update_lane_load(self, lane):
        # Update the load part of the UI
        # Assuming there's a widget or method to update the load status
        # lane_box.show_all()
        pass

    def update_system_container(self):
        """
        Update the labels for the current load, extruder, and buffer in the UI.
        If any value is None, display 'N/A'.
        """
        loaded_label = self.labels.get("loaded_label")
        extruder_label = self.labels.get("extruder_label")
        buffer_label = self.labels.get("buffer_label")

        # Check if current_load is None
        if not self.afc_system or not self.afc_system.current_load:
            # Set all labels to "N/A" if current_load is None
            if loaded_label:
                loaded_label.set_label("Loaded: N/A")
            if extruder_label:
                extruder_label.set_label("Extruder: N/A")
            if buffer_label:
                buffer_label.set_label(self.format_lane_sensor_label(None))
            return

        # Get the current lane object
        current_lane = next((lane for lane in self.afc_lane_data if lane.name == self.afc_system.current_load), None)

        # Update the loaded label
        if loaded_label:
            loaded_text = f"Loaded: {self.afc_system.current_load}" if self.afc_system.current_load else "Loaded: N/A"
            loaded_label.set_label(loaded_text)

        # Update the extruder label
        if extruder_label:
            extruder_text = f"Extruder: {current_lane.extruder}" if current_lane and current_lane.extruder else "Extruder: N/A"
            extruder_label.set_label(extruder_text)

        # Update the buffer label
        if buffer_label:
            buffer_label.set_label(self.format_lane_sensor_label(current_lane))

    def on_lane_map_changed(self, menu_button, selected_value, lane, label):
        """
        Triggered when a new lane mapping is selected from the MenuButton.

        :param menu_button: The Gtk.MenuButton widget.
        :param selected_value: The T-value selected (e.g., "T0").
        :param lane: The current lane object.
        :param label: The label inside the MenuButton that displays the current value.
        """
        if selected_value and selected_value != lane.map:
            old_mapping = lane.map
            lane.map = selected_value  # Update lane map

            logging.info(f"Updated default mapping for {lane.name}: {old_mapping} → {selected_value}")

            # Update the label text in the MenuButton
            label.set_text(selected_value)

            # Send G-code to update the mapping
            self._screen._send_action(menu_button, "printer.gcode.script", {
                "script": f"SET_MAP LANE={lane.name} MAP={selected_value}"
            })

            # Optionally refresh UI
            self.refresh_lane_dropdowns()


    def refresh_lane_dropdowns(self):
        """
        Refresh all lane map dropdowns to reflect the current mappings.
        """
        for unit in self.afc_units:
            for lane in unit.lanes:
                self.update_lane_map(lane)

    def replace_lane_info_grid(self, lane, lane_name, status):
        # Cancel any pending update for this lane
        if hasattr(self, '_pending_lane_grid_updates') and lane.name in self._pending_lane_grid_updates:
            GLib.source_remove(self._pending_lane_grid_updates[lane.name])

        def do_update():
            old_grid = self.labels.get(f"{lane.name}_lane_info_grid")
            parent = old_grid.get_parent() if old_grid else None
            if old_grid and parent:
                parent.remove(old_grid)
            # Rebuild parent if lost
            if not parent:
                # Find the unit and re-add the lane frame/grid to the unit's lane_grid
                for unit in self.afc_units:
                    for l in unit.lanes:
                        if l.name == lane.name:
                            # Find the unit_expander and lane_grid
                            # This is pseudo-code, you may need to adapt it:
                            for child in unit_expander.get_children():
                                if isinstance(child, AutoGrid):
                                    parent = child
                                    break
            new_grid = self.create_lane_info_grid(lane, lane_name, status)
            if parent:
                parent.add(new_grid)
                new_grid.show_all()
            # Remove the pending update handle
            del self._pending_lane_grid_updates[lane.name]
            return False  # Only run once

        # Schedule the update after 50ms
        self._pending_lane_grid_updates[lane.name] = GLib.timeout_add(50, do_update)

    def update_lane_ui(self, lane):
        lane_box = self.lane_widgets.get(lane.name)
        if not lane_box:
            return

        # Update lane info box
        self.update_lane_info_box(lane)

        lane_box.show_all()


    ##################
    #    Callbacks   #
    ##################

    def on_icon_button_clicked(self, button, lane):
        print(f"Icon button clicked for lane: {lane.name}")

    def on_map_button_clicked(self, button, lane):
        print(f"Map button clicked: {lane.map}")

    def on_lane_inf_changed(self, menu_button, value, lane, label):
        if value and value != lane.map:
            old_runout = lane.runout_lane
            lane.runout_lane = value  # Update lane map
            logging.info(f"Updated default mapping for {lane.name}: {old_runout} → {value}")
            label.set_text(f"{value} ∞")


            # Send G-code to update the mapping
            self._screen._send_action(menu_button, "printer.gcode.script", {
                "script": f"SET_RUNOUT LANE={lane.name} RUNOUT={value}"
            })


    def on_load_lane_clicked(self, button, lane):
        logging.info(f"Load Lane button clicked for {lane.name}")
        self._screen._send_action(button, "printer.gcode.script", {"script": f"CHANGE_TOOL LANE={lane.name}"})

    def on_eject_lane_clicked(self, button, lane):
        logging.info(f"Eject Lane button clicked for {lane.name}")
        self._screen._send_action(button, "printer.gcode.script", {"script": f"LANE_UNLOAD LANE={lane.name}"})

    def on_unload_lane_clicked(self, button, lane):
        logging.info(f"Unload Lane button clicked for {lane.name}")
        self._screen._send_action(button, "printer.gcode.script", {"script": f"TOOL_UNLOAD"})

    #################
    #    Status     #
    #################

    def get_lane_status(self, lane):
        if lane.tool_loaded:
            status = TOOLED
        elif lane.prep and lane.load and not lane.tool_loaded:
            status = LOADED
        elif lane.prep and not lane.load:
            status = PREP_NOT_LOAD
        elif lane.load and not lane.prep:
            status = LOAD_NOT_PREP
        else: 
            status = UNLOADED

        return status

    def get_lane_status_from_data(self, lane_data):
        status = lane_data.get("status", False)
        # logging.info(f"get status: {status}")
        if lane_data.get("prep") and lane_data.get("load"):
            if status == "Tool Loading":
                return LOADING
            elif status == "Tool Unloading":
                return UNLOADING
            elif lane_data.get("tool_loaded"):
                return TOOLED
            elif not lane_data.get("tool_loaded"):
                return LOADED

        elif lane_data.get("prep") and not lane_data.get("load"):
            return PREP_NOT_LOAD
        elif lane_data.get("load") and not lane_data.get("prep"):
            return LOAD_NOT_PREP
        else:
            return UNLOADED

    def set_lane_status(self, lane, status):
        logging.info(f"Lane {lane.name} status: {status}")
        style = []
        if status == TOOLED:
            style.append("status-tooled")
            style.append("bold-text")
        elif status == LOADED:
            style.append("status-loaded")
        elif status == PREP_NOT_LOAD:
            style.append("status-warning")
        elif status == LOAD_NOT_PREP:
            style.append("status-warning")
        else:
            style.append("status-lane-empty")

        return style

    ##################
    #    Dropdowns   #
    ##################

    def create_lane_dropdown(self):
        """
        Create a dropdown (Gtk.ComboBox) for selecting lanes with enhanced behavior.
        """
        # Create a ListStore to hold the lane names
        lane_store = Gtk.ListStore(str)
        for lane in self.get_afc_lanes():
            lane_store.append([lane])  # Add each lane name to the store

        # Create the dropdown
        dropdown = Gtk.ComboBox.new_with_model(lane_store)
        dropdown.set_vexpand(True)
        renderer_text = Gtk.CellRendererText()
        dropdown.pack_start(renderer_text, True)
        dropdown.add_attribute(renderer_text, "text", 0)
        dropdown.set_active(0)  # Set the first lane as the default selection

        dropdown.set_direction(Gtk.ArrowType.UP)

        # Set the default move_lane to the first lane
        if self.afc_lanes:
            self.move_lane = self.afc_lanes[0]

        # Connect the "changed" signal to handle lane selection
        dropdown.connect("changed", self.on_lane_selected)

        # Connect the "notify::popup-shown" signal to handle dropdown behavior
        dropdown.connect("notify::popup-shown", self.on_popup_shown)

        # Add a label above the dropdown
        dropdown_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        dropdown_box.pack_start(dropdown, False, False, 0)

        self.dropdown = dropdown  # Store the dropdown for later use
        self.last_drop_time = None  # Track the last time the dropdown was opened

        return dropdown_box

    def create_distance_grid(self):
        """
        Create a grid of buttons to select the move distance.
        """
        distbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        distbox.set_hexpand(False)
        self.labels['move_dist'] = Gtk.Label(label="Distance (mm)")
        self.labels['move_dist'].set_halign(Gtk.Align.CENTER)
        distbox.pack_start(self.labels['move_dist'], True, True, 0)
        distbox.set_margin_bottom(25)

        distance_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        distance_box.set_halign(Gtk.Align.CENTER)

        # Create the distance entry and add it to self.labels
        self.labels['distance_entry'] = Gtk.Entry()
        self.labels['distance_entry'].set_hexpand(True)
        self.labels['distance_entry'].set_placeholder_text("Enter distance")
        self.labels['distance_entry'].set_text(str(self.distance))
        self.labels['distance_entry'].set_valign(Gtk.Align.FILL)
        self.labels['distance_entry'].set_halign(Gtk.Align.CENTER)
        self.labels['distance_entry'].set_size_request(150, -1)
        self.labels['distance_entry'].set_width_chars(4)
        self.labels['distance_entry'].set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.labels['distance_entry'].connect("focus-in-event", self.show_keyboard)
        self.labels['distance_entry'].connect("focus-out-event", self._screen.remove_keyboard)
        self.labels['distance_entry'].connect("changed", self.on_distance_changed)
        distance_box.pack_start(self.labels['distance_entry'], True, True, 0)

        # Create the distance grid
        distgrid = Gtk.Grid(column_homogeneous=True)
        distgrid.set_column_spacing(1)
        self.distances = ['5', '10', '25', '50']  # Define available distances
        for j, i in enumerate(self.distances):
            self.labels[f"dist{i}"] = Gtk.Button(label=i)
            self.labels[f"dist{i}"].connect("clicked", self.change_distance, int(i))
            ctx = self.labels[f"dist{i}"].get_style_context()
            ctx.add_class("horizontal_togglebuttons")
            if int(i) == self.distance:
                ctx.add_class("horizontal_togglebuttons_active")
            distgrid.attach(self.labels[f"dist{i}"], (j+1), 0, 1, 1)
            self.labels[f"dist{i}"].set_hexpand(True)

        distance_box.pack_start(distgrid, True, True, 0)
        distbox.add(distance_box)
        return distbox

    def on_distance_changed(self, entry):
        """
        Handle distance entry changes and update the grid selection.
        """
        distance = int(entry.get_text())
        if distance > 0:
            self.distance = distance

            # Highlight the corresponding button if it matches a predefined distance
            for dist in self.distances:
                button = self.labels.get(f"dist{dist}")
                if button:
                    ctx = button.get_style_context()
                    if int(dist) == distance:
                        ctx.add_class("horizontal_togglebuttons_active")
                    else:
                        ctx.remove_class("horizontal_togglebuttons_active")
        else:
            # Clear highlights if the input is invalid
            self.clear_grid_selection()

    def change_distance(self, widget, distance):
        """
        Update the selected distance and highlight the active button.
        """
        # Remove the active class from the previously selected button
        previous_button = self.labels.get(f"dist{self.distance}")
        if previous_button:
            previous_button.get_style_context().remove_class("horizontal_togglebuttons_active")

        # Add the active class to the newly selected button
        widget.get_style_context().add_class("horizontal_togglebuttons_active")

        # Update the distance and the entry
        self.distance = distance
        self.labels['distance_entry'].set_text(str(distance))

    def clear_grid_selection(self):
        """
        Clear the selection in the grid.
        """
        for dist in ['5', '10', '25', '50']:
            button = self.labels.get(f"dist{dist}")
            if button:
                button.get_style_context().remove_class("horizontal_togglebuttons_active")

    def on_lane_selected(self, dropdown):
        """
        Handle lane selection from the dropdown and update self.move_lane.
        """
        model = dropdown.get_model()
        active_iter = dropdown.get_active_iter()
        if active_iter is not None:
            self.move_lane = model[active_iter][0]  # Update the selected lane
            logging.info(f"Selected lane: {self.move_lane}")
        else:
            logging.warning("No lane selected.")

    def on_popup_shown(self, combo_box, param):
        """
        Handle the dropdown's popup-shown signal to detect when it opens or closes.
        """
        if combo_box.get_property("popup-shown"):
            logging.debug("Dropdown popup show")
            self.last_drop_time = datetime.now()
        else:
            elapsed = (datetime.now() - self.last_drop_time).total_seconds()
            if elapsed < 0.2:  # If the dropdown closes too quickly
                logging.debug(f"Dropdown closed too fast ({elapsed}s)")
                GLib.timeout_add(50, self.dropdown_keep_open)
                return
            logging.debug("Dropdown popup close")

    def dropdown_keep_open(self):
        """
        Reopen the dropdown if it closes too quickly.
        """
        self.dropdown.popup()
        return False

    def on_move_button_clicked(self, button):
        """
        Handle the move button click and send the move command.
        """
        if not self.move_lane:
            logging.warning("Move command failed: No lane selected.")
            return

        logging.info(f"Moving to lane: {self.move_lane} with distance: {self.distance}")
        self._screen._send_action(button, "printer.gcode.script", {
            "script": f"LANE_MOVE LANE={self.move_lane} DISTANCE={self.distance}"
        })

    def on_neg_move_button_clicked(self, button):
        """
        Handle the move button click and send the move command.
        """
        if not self.move_lane:
            logging.warning("Move command failed: No lane selected.")
            return

        logging.info(f"Moving to lane: {self.move_lane} with distance: {self.distance}")
        self._screen._send_action(button, "printer.gcode.script", {
            "script": f"LANE_MOVE LANE={self.move_lane} DISTANCE=-{self.distance}"
        })

    ##################
    # Spool Selector #
    ##################

    def create_spool_layout(self):
        """
        Create a layout for changing the spool, including input boxes in a scroll box,
        and dynamically show/hide the keyboard or keypad.
        """
        logging.info("Creating spool selector layout")

        self.input_widgets = {}

        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_box.set_hexpand(False)
        main_box.set_vexpand(True)

        # Scrollable container for input boxes
        self.spool_scroll = Gtk.ScrolledWindow()
        self.spool_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.spool_scroll.set_hexpand(False)
        self.spool_scroll.set_vexpand(True)

        input_grid = AutoGrid()
        input_grid.set_row_homogeneous(False)
        input_grid.set_column_homogeneous(False)
        input_grid.set_halign(Gtk.Align.CENTER)
        input_grid.set_valign(Gtk.Align.CENTER)
        input_grid.set_column_spacing(15)
        input_grid.set_row_spacing(5)

        # Title
        title_label = Gtk.Label(label="Change Spool")
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.set_valign(Gtk.Align.CENTER)
        title_label.get_style_context().add_class("bold-text")
        self.labels['title_label'] = title_label  # Store the title label
        input_grid.attach(title_label, 0, 0, 2, 1)  # Span across two columns
        self.labels["title_label"].grab_focus

        # Color Selector
        color_label = Gtk.Label(label="Filament Color")
        color_label.set_halign(Gtk.Align.START)
        color_label.set_valign(Gtk.Align.CENTER)
        color_label.get_style_context().add_class("bold-text")

        color_selector = self.create_color_selector()
        color_selector.set_hexpand(False)
        # color_selector.set_halign(Gtk.Align.START)
        input_grid.attach(color_label, 0, 1, 1, 1)
        input_grid.attach(color_selector, 1, 1, 1, 1)

        # Filament Type Selector
        type_label = Gtk.Label(label="Filament Type")
        type_label.set_halign(Gtk.Align.START)
        type_label.set_valign(Gtk.Align.CENTER)
        type_label.get_style_context().add_class("bold-text")
        type_input = Gtk.Entry()
        type_input.set_hexpand(False)
        type_input.set_placeholder_text("Enter filament type (e.g., PLA)")
        type_input.connect("focus-in-event", self.show_keyboard)
        type_input.connect("focus-out-event", self._screen.remove_keyboard)
        self.labels["type_input"] = type_input  # Store the input widget
        input_grid.attach(type_label, 0, 2, 1, 1)
        input_grid.attach(type_input, 1, 2, 1, 1)

        # Weight Input
        weight_label = Gtk.Label(label="Remaining Weight")
        weight_label.set_halign(Gtk.Align.START)
        weight_label.set_valign(Gtk.Align.CENTER)
        weight_label.get_style_context().add_class("bold-text")
        weight_input = Gtk.Entry()
        weight_input.set_hexpand(False)
        weight_input.set_placeholder_text("Enter weight (e.g., 700)")
        weight_input.set_input_purpose(Gtk.InputPurpose.NUMBER)
        weight_input.connect("focus-in-event", self.show_keyboard)
        weight_input.connect("focus-out-event", self._screen.remove_keyboard)
        self.labels["weight_input"] = weight_input  # Store the input widget
        input_grid.attach(weight_label, 0, 3, 1, 1)
        input_grid.attach(weight_input, 1, 3, 1, 1)

        # Buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_hexpand(False)
        button_box.set_vexpand(False)

        return_button = self._gtk.Button("back", _("Return"), "color2")
        return_button.set_halign(Gtk.Align.CENTER)
        return_button.set_valign(Gtk.Align.CENTER)
        return_button.connect("clicked", self.show_main_grid)
        button_box.pack_start(return_button, True, True, 5)

        update_button = self._gtk.Button("complete", _("Update"), "color1", scale=1.5)
        update_button.set_halign(Gtk.Align.CENTER)
        update_button.set_valign(Gtk.Align.CENTER)
        update_button.connect("clicked", self.on_update_spool_clicked)
        button_box.pack_start(update_button, True, True, 5)

        weight_input.connect("changed", self.on_weight_changed)
        type_input.connect("changed", self.on_type_changed)

        input_grid.attach(button_box, 0, 4, 2, 1)  # Span across two columns

        # Add input grid to the scroll container
        self.spool_scroll.add(input_grid)

        main_box.pack_start(self.spool_scroll, False, False, 5)

        # Attach the overlay to the selector grid
        self.selector_grid.attach(main_box, 0, 0, 1, 1)

    def on_weight_changed(self, entry):
        self.selected_weight = entry.get_text()

    def on_type_changed(self, entry):
        self.selected_type = entry.get_text()

    def show_keyboard(self, entry, event):
        self._screen.show_keyboard(entry, event)
        GLib.timeout_add(100, self.scroll_to_entry, entry)

    def scroll_to_entry(self, entry):
        """
        Scroll the view to ensure the specified entry is visible.
        """
        if self.spool_scroll:
            adjustment = self.spool_scroll.get_vadjustment()
            allocation = entry.get_allocation()
            adjustment.set_value(allocation.y - 50)  # Adjust the scroll position

    def create_color_selector(self):
        """
        Create a touch-friendly color selector for the filament.
        """
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        color_box.set_hexpand(False)
        color_selector_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # RGB input fields
        rgb_box = Gtk.Grid()
        rgb_box.set_column_homogeneous(False)
        rgb_box.set_row_spacing(5)
        rgb_box.set_column_spacing(5)

        self.r_input = Gtk.Entry()
        self.r_input.set_hexpand(True)
        self.r_input.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.r_input.connect("focus-in-event", self.show_keyboard)
        self.r_input.connect("focus-out-event", self._screen.remove_keyboard)
        self.r_input.set_placeholder_text("R")
        self.r_input.set_width_chars(3)

        self.g_input = Gtk.Entry()
        self.g_input.set_hexpand(True)
        self.g_input.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.g_input.connect("focus-in-event", self.show_keyboard)
        self.g_input.connect("focus-out-event", self._screen.remove_keyboard)
        self.g_input.set_placeholder_text("G")
        self.g_input.set_width_chars(3)

        self.b_input = Gtk.Entry()
        self.b_input.set_hexpand(True)
        self.b_input.connect("focus-in-event", self.show_keyboard)
        self.b_input.connect("focus-out-event", self._screen.remove_keyboard)
        self.b_input.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.b_input.set_placeholder_text("B")
        self.b_input.set_width_chars(3)

        self.r_input.connect("changed", self.on_rgb_changed)
        self.g_input.connect("changed", self.on_rgb_changed)
        self.b_input.connect("changed", self.on_rgb_changed)

        rgb_box.attach(Gtk.Label(label="R:"), 0, 0, 1, 1)
        rgb_box.attach(self.r_input, 1, 0, 1, 1)
        rgb_box.attach(Gtk.Label(label="G:"), 2, 0, 1, 1)
        rgb_box.attach(self.g_input, 3, 0, 1, 1)
        rgb_box.attach(Gtk.Label(label="B:"), 4, 0, 1, 1)
        rgb_box.attach(self.b_input, 5, 0, 1, 1)

        color_selector_box.pack_start(rgb_box, True, True, 0)

        # HEX input field
        hex_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hex_label = Gtk.Label(label="HEX:")
        hex_label.get_style_context().add_class("bold-text")
        self.hex_input = Gtk.Entry()
        self.hex_input.connect("changed", self.on_hex_changed)
        self.hex_input.connect("focus-in-event", self.show_keyboard)
        self.hex_input.connect("focus-out-event", self._screen.remove_keyboard)
        self.hex_input.set_placeholder_text("#RRGGBB")
        self.hex_input.set_width_chars(7)

        # Color preview and button
        self.color_button = Gtk.ColorButton()  # Store a reference to the color buttons
        self.color_button.get_style_context().add_class("color-button")
        self.color_button.set_title("Select Filament Color")
        self.color_button.set_rgba(Gdk.RGBA(1, 0, 0, 1))  # Default to red
        self.color_button.set_use_alpha(False)
        self.color_button.set_size_request(200, 75)
        self.color_button.connect("color-set", self.on_color_selected)

        hex_box.pack_start(hex_label, False, False, 0)
        hex_box.pack_start(self.hex_input, True, True, 0)
        color_selector_box.pack_start(hex_box, False, False, 0)
        color_box.pack_start(self.color_button, True, True, 0)
        color_box.pack_start(color_selector_box, True, True, 0)

        return color_box

    def on_rgb_changed(self, entry):
        """
        Update the color button and HEX field when RGB values are changed.
        """
        try:
            r = int(self.r_input.get_text() or 0)
            g = int(self.g_input.get_text() or 0)
            b = int(self.b_input.get_text() or 0)

            # Clamp values between 0 and 255
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))

            # Update the color button
            self.color_button.set_rgba(Gdk.RGBA(r / 255, g / 255, b / 255, 1))

            # Update the HEX field
            self.selected_color = f"#{r:02X}{g:02X}{b:02X}"
            self.hex_input.set_text(self.selected_color)
        except ValueError:
            pass  # Ignore invalid input

    def on_hex_changed(self, entry):
        """
        Update the color button and RGB fields when the HEX value is changed.
        """
        hex_value = entry.get_text().lstrip("#")
        if len(hex_value) == 6:
            try:
                r = int(hex_value[0:2], 16)
                g = int(hex_value[2:4], 16)
                b = int(hex_value[4:6], 16)

                # Update the color button
                self.color_button.set_rgba(Gdk.RGBA(r / 255, g / 255, b / 255, 1))

                # Update the RGB fields
                self.r_input.set_text(str(r))
                self.g_input.set_text(str(g))
                self.b_input.set_text(str(b))
            except ValueError:
                pass  # Ignore invalid HEX values


    def on_color_selected(self, color_button):
        """
        Update the RGB and HEX fields when a color is selected from the color button.
        """
        rgba = color_button.get_rgba()
        r = int(rgba.red * 255)
        g = int(rgba.green * 255)
        b = int(rgba.blue * 255)

        # Update RGB fields
        self.r_input.set_text(str(r))
        self.g_input.set_text(str(g))
        self.b_input.set_text(str(b))

        # Update HEX field
        self.selected_color = f"#{r:02X}{g:02X}{b:02X}"
        self.hex_input.set_text(self.selected_color)

    def on_update_spool_clicked(self, button):
        """
        Handle the 'Update Spool' button click event.
        """
        logging.info(f"Update Spool button clicked with color: {self.selected_color}")

        if self.spoolman is not None:
            self._screen._send_action(button, "printer.gcode.script", {
                "script": f'SET_SPOOL_ID LANE={self.selected_lane.name} SPOOL_ID=""'
            })

        # Handle color
        try:
            color = self.selected_color.lstrip("#")
            if color:
                self._screen._send_action(button, "printer.gcode.script", {
                    "script": f"SET_COLOR LANE={self.selected_lane.name} COLOR={color}"
                })
        except AttributeError:
            logging.warning("Selected color is not set or invalid. Skipping color update.")

        # Handle filament type
        try:
            filament_type = self.selected_type
            if filament_type:
                self._screen._send_action(button, "printer.gcode.script", {
                    "script": f"SET_MATERIAL LANE={self.selected_lane.name} MATERIAL={filament_type.upper()}"
                })
        except AttributeError:
            logging.warning("Selected filament type is not set or invalid. Skipping material update.")

        # Handle weight
        try:
            weight = self.selected_weight
            if weight:
                self._screen._send_action(button, "printer.gcode.script", {
                    "script": f"SET_WEIGHT LANE={self.selected_lane.name} WEIGHT={weight}"
                })
        except AttributeError:
            logging.warning("Selected weight is not set or invalid. Skipping weight update.")

        # Handle lane
        try:
            lane = self.selected_lane
            if not lane:
                logging.warning("Selected lane is not set or invalid. Skipping lane update.")
        except AttributeError:
            logging.warning("Selected lane is not set or invalid. Skipping lane update.")

        # Return to the main grid
        self.show_main_grid(button)

    ##################
    #    Sensors     #
    ##################

    def sensor_layout(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_hexpand(False)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)

        self.sensor_labels = {}

        screen_width = self._screen.width
        # Fetch the current sensor states before passing to create_sensor_grid
        sensor_data = self.fetch_sensor_data()
        sensors_grid = self.create_sensor_grid(self.filament_sensors, screen_width, sensor_data)
        vbox.pack_start(sensors_grid, True, True, 0)

        refresh_button = Gtk.Button(label="Refresh")
        refresh_button.get_style_context().add_class("button_active")
        refresh_button.connect("clicked", self.on_refresh_clicked)
        vbox.pack_start(refresh_button, False, False, 0)

        self.sensor_grid.attach(vbox, 0, 0, 1, 1)

    def create_sensor_grid(self, sensors, screen_width, sensor_data):
        # Main grid
        grid = AutoGrid()
        grid.set_hexpand(False)
        grid.set_vexpand(True)
        grid.set_column_spacing(20)  # Add more space between columns
        grid.set_row_spacing(5)
        grid.set_column_homogeneous(False)

        # Custom sorting function
        def sensor_sort_key(sensor_name):
            # Assign priorities based on sensor type
            if "prep" in sensor_name.lower():
                priority = 0  # Highest priority for "prep" sensors
            elif "load" in sensor_name.lower():
                priority = 1  # Second priority for "load" sensors
            else:
                priority = 2  # Lowest priority for other sensors

            # Extract the numeric part of the sensor name for secondary sorting
            match = re.search(r'\d+', sensor_name)
            numeric_part = int(match.group()) if match else float('inf')

            # Return a tuple for sorting: (priority, numeric part)
            return (priority, numeric_part)

        # Sort sensors using the custom key
        sensors = sorted(sensors, key=sensor_sort_key)

        # Group sensors into "prep", "load", and "others"
        prep_sensors = [s for s in sensors if "prep" in s.lower()]
        load_sensors = [s for s in sensors if "load" in s.lower()]
        other_sensors = [s for s in sensors if s not in prep_sensors and s not in load_sensors]

        # Arrange sensors into columns
        columns = [prep_sensors, load_sensors, other_sensors]

        for col_index, column in enumerate(columns):
            for row_index, sensor_name in enumerate(column):
                label_name = sensor_name.split("filament_switch_sensor ", 1)[-1].strip()
                label_name = label_name.replace("_", " ").capitalize()

                # Create the sensor label
                sensor_label = Gtk.Label(label=label_name)
                sensor_label.set_halign(Gtk.Align.END)
                sensor_label.set_hexpand(False)

                # Create the status dot
                status_dot = Gtk.EventBox()
                dot = Gtk.Label(label=" ")
                dot.set_size_request(20, 20)
                dot.set_valign(Gtk.Align.CENTER)
                dot.set_halign(Gtk.Align.START)

                style = dot.get_style_context()
                filament_detected = sensor_data.get(sensor_name, {}).get("filament_detected", False)
                style.remove_class("status-empty")
                style.remove_class("status-active")
                style.add_class("status-active" if filament_detected else "status-empty")

                status_dot.add(dot)

                # Store the label and dot for updates
                self.sensor_labels[sensor_name] = {"label": sensor_label, "dot": dot}

                # Attach the label and dot to the grid
                grid.attach(sensor_label, col_index * 3, row_index, 1, 1)  # Name column
                grid.attach(status_dot, col_index * 3 + 1, row_index, 1, 1)  # Dot column

        # Add the grid to a scrolled window
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(False)
        scrolled_window.set_vexpand(True)
        scrolled_window.add(grid)

        # Create Return button
        return_button = self._gtk.Button("back", _("Back"), "color3")
        return_button.set_halign(Gtk.Align.END)
        return_button.set_valign(Gtk.Align.START)
        return_button.set_margin_top(10)
        return_button.set_margin_end(10)

        return_button.connect("clicked", self.show_main_grid)  # Define this handler in your class

        # Overlay button on top of the scrolled window
        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        overlay.add(scrolled_window)
        overlay.add_overlay(return_button)

        return overlay

    def fetch_sensor_data(self):
        """
        Fetches the current filament sensor data from the printer API.
        :return: Dictionary containing the status of the sensors.
        """
        # Here we send the request and parse the result into a dictionary
        logging.info("Fetching filament sensor data from the printer API")
        sensor_query = "&".join(self.filament_sensors)
        result = self.apiClient.send_request(f"printer/objects/query?{sensor_query}")
        sensor_data = result.get("status", {})
        return sensor_data

    def update_sensors(self, data):
        """
        Updates the sensor dots based on the latest data.
        :param data: The latest sensor data from the API.
        """
        for sensor_name, elements in self.sensor_labels.items():
            dot = elements["dot"]
            sensor_data = data.get(sensor_name)
            if sensor_data and isinstance(sensor_data, dict):
                detected = sensor_data.get("filament_detected", False)
                style = dot.get_style_context()
                style.remove_class("status-empty")
                style.remove_class("status-active")
                style.add_class("status-active" if detected else "status-empty")

        sensor_name = "filament_switch_sensor virtual_bypass"
        vb_status = data.get(sensor_name, {}).get("filament_detected", False)
        self.update_virtual_bypass_toggle(vb_status)

    def on_refresh_clicked(self, button):
        """
        Handles the refresh button click event to update the sensor states.
        :param button: The refresh button widget.
        """
        if not self.filament_sensors:
            return
        # Fetch the latest sensor data
        sensor_data = self.fetch_sensor_data()
        # Update the sensor grid
        self.update_sensors(sensor_data)

    def log_lane_widget_sizes(self):
        """One-shot: log frame + all children sizes for each lane."""
        logging.info("=== AFC lane widget dump start ===")
        for unit in self.afc_units:
            for lane in unit.lanes:
                frame = self.lane_widgets.get(f"{lane.name}_frame")
                if not frame:
                    continue
                logging.info(f"Lane: {lane.name} (frame={frame})")
                # recurse and log
                def dump_widget(w, path=""):
                    try:
                        alloc = w.get_allocation()
                    except Exception:
                        alloc = None
                    try:
                        pref_min, pref_nat = w.get_preferred_height()
                    except Exception:
                        pref_min = pref_nat = None
                    vexp = getattr(w, "get_vexpand", lambda: False)()
                    hexp = getattr(w, "get_hexpand", lambda: False)()
                    classes = []
                    try:
                        classes = list(w.get_style_context().list_classes())
                    except Exception:
                        pass
                    logging.info(f"  {path}{w.__class__.__name__}: alloc={(alloc.width if alloc else None, alloc.height if alloc else None)}, preferred=(min={pref_min},nat={pref_nat}), vexpand={vexp}, hexpand={hexp}, css={classes}")
                    # If parent is a Box, get packing info for this child
                    parent = w.get_parent()
                    try:
                        if isinstance(parent, Gtk.Box):
                            expand, fill, padding, packtype = parent.query_child_packing(w)
                            logging.info(f"    packing expand={expand}, fill={fill}, padding={padding}, packtype={packtype}")
                    except Exception:
                        pass
                    # connect one-time size-allocate to catch later changes
                    try:
                        w.connect("size-allocate", self.on_size_allocate, f"{lane.name}:{path}{w.__class__.__name__}")
                    except Exception:
                        pass
                    # Recurse into children if container
                    children = []
                    try:
                        # Gtk.Frame: get_child(), Overlay: get_children(), others: get_children()
                        if isinstance(w, Gtk.Frame):
                            child = w.get_child()
                            if child:
                                children = [child]
                        elif isinstance(w, Gtk.Overlay):
                            children = w.get_children()
                        elif hasattr(w, "get_children"):
                            children = w.get_children()
                    except Exception:
                        children = []
                    for i, c in enumerate(children):
                        dump_widget(c, path=path + f"{w.__class__.__name__}[{i}]/")
                dump_widget(frame, path="")
        logging.info("=== AFC lane widget dump end ===")
        return False  # for GLib.idle_add: run once

    def on_size_allocate(self, widget, allocation, name=None):
        logging.info(f"size-allocate: {name or widget.get_name()} -> w={allocation.width}, h={allocation.height}")


###################
#    CSS Styles   #
###################
# Additional CSS to style the lane boxes
inline_provider = Gtk.CssProvider()
inline_provider.load_from_data(b"""
dialog label {
    color: white;
}
dialog button {
    background-color: rgba(30,30,30, 0.85);
    color: white;
    border-radius: 10px;
}
.scroll_button{
    background-color: rgba(35, 35, 35, 0.85);
}
.bold-text {
    font-weight: bold;
}
.large-button {
    font-size: 20px;
    padding-left: 40px;
    padding-right: 40px;
    padding-top: 10px;
    padding-bottom: 10px;
}
.control-button {
    font-size: 25px;
    padding-top: 15px;
    padding-bottom: 15px;
}
.small-scroll {
    -GtkScrollbar-width: 2px;
}
.color-button {
    border: 2px solid #ffffff;
    border-radius: 20px;
    padding: 10px;
    margin: 10px;
}
.close-box {
    background-color: rgba(128, 40, 40, 0.90);
    border-radius: 5px;
    padding: 10px;
}
.extruder-box {
    background-color: rgba(50, 50, 50, 0.4);
    border-radius: 8px;
    padding: 10px;
}
.lane-box {
    background-color: rgba(70, 70, 70, 0.7);
    border-radius: 8px;
    padding: 4px;
}
.header-box {
    background-color: rgba(70, 70, 70, 0.7);
    border-radius: 8px;
    padding: 10px;
    color: rgb(0,0,0);
}
.load-box {
    background-color: rgba(40, 40, 40, 0.90);
    border-radius: 8px;
    min-height: 250px;
}
.lane-back {
    background-color: rgba(50, 50, 50, 0.85);
    border-radius: 5px;
    padding: 5px;
}
.action-button {
    background-color: rgba(50, 50, 50, 0.85);
    border-color: @color3;
    border-style: solid;
    border-width: 4px;
    border-radius: 8px;
    padding-top: 15px;
    padding-bottom: 15px;
}
.action-button-small {
    background-color: rgba(50, 50, 50, 0.85);
    border-color: @color3;
    border-style: solid;
    border-width: 4px;
    border-radius: 8px;
    padding: 5px;
}
.no-background {
    background-color: rgba(50, 50, 50, 0.0);
    border-radius: 5px;
}
.highlighted-lane {
    border: 2px solid #429ef5;
    border-radius: 1em;
    border-width: 4px;
}
.status-loaded {
    color: #48bf53; /* Replace @color3 with a valid color */
}
.status-lane-empty {
    color: #e32929; /* Replace @color1 with a valid color */
}
.status-warning {
    color: #eb8510; /* Replace @color4 with a valid color */
}
.status-tooled {
    color: #429ef5;
}
.combo-no-arrow box arrow {
    padding: 0px;
    margin: 0;
    opacity: 0;
    min-width: 0;
    min-height: 0;
}
.filament_sensor_empty {
    background-color: @active; /* Red for empty */
}
.filament_sensor_detected {
    background-color: @echo; /* Green for detected */
}
.status-active {
    background-color: #48bf53;
    border-radius: 25px;
}
.status-empty {
    background-color: #e32929;
    border-radius: 25px;
}
.vb_active {
    box-shadow: 0 1px 12px 0 #378a3f;
    padding-bottom: 0.1em;
    border-bottom: .4em solid #48bf53;
}
.vb_inactive {
    box-shadow: inset 0 4px 12px 0 rgba(100,100,100,0.4);
    padding: 0.33em;
    padding-bottom: 0.1em;
    border-bottom: .4em solid #8c8c8c;
}
""")

Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(),
    inline_provider,
    Gtk.STYLE_PROVIDER_PRIORITY_USER
)
