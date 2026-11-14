"""Settings actions and read-only presentation snapshots; never poll hardware here."""
from __future__ import annotations

import copy
import os
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from . import icons
from providers.base import DeviceStatus

log = logging.getLogger("halo_battery")


class SettingsController:
    def __init__(self, app):
        self.app = app
        self.context = app.settings_context()
        self._preview_state = None
        self._preview_art = None
        self._catalog = None
        self._catalog_lock = threading.Lock()
        self._icon_selection_revisions = {}
        self._icon_upgrade_started = False

    def _statuses(self):
        app = self.app
        result = {st.key: st for st in getattr(app, "hid_results", [])}
        result.update((st.key, st) for st in getattr(app, "bt_cache", []))
        result.update((key, owner.status) for key, owner in app.icons.items() if owner.status is not None)
        for key, name in app._settings_map("hidden").items():
            if isinstance(key, str) and key not in result:
                result[key] = DeviceStatus(key, str(name), online=False)
        return result

    def snapshot(self):
        app, ctx = self.app, self.context
        with app.lock:
            cfg = copy.deepcopy(app.cfg)
            devices = []
            for key, st in self._statuses().items():
                brand, color = app.controller_style(st)
                choice = app._settings_map("icons").get(key, "")
                if not isinstance(choice, str) or choice not in icons.PICTOGRAM_KINDS:
                    choice = ""
                devices.append(dict(key=key, name=app.display_name(st), kind=ctx["device_kind"](st), status=copy.deepcopy(st),
                    estimate=app.time_left_text(st), controller=app.pictogram(st) in ctx["controller_pictograms"],
                    custom_icon_id=cfg.get("custom_icons", {}).get(key, "") if isinstance(cfg.get("custom_icons"), dict) else "",
                    custom_icon_name=getattr(getattr(app, "_custom_assets", {}).get(
                        app._settings_map("custom_icons").get(key)), "icon_id", ""),
                    pictogram=choice, brand=brand, brand_color=color, low_override=app.device_low(key),
                    hidden=key in app._settings_map("hidden"), provider=app.key_provider.get(key, st.source)))
            disabled = app.disabled_providers()
            release = getattr(app, "update", None)
            result = dict(cfg=cfg, devices=devices, version=ctx["version"], data_dir=ctx["data_dir"],
                          status_path=ctx["status_path"], update=dict(version=release.version, url=release.url) if release else None,
                          update_progress=getattr(app, "update_progress", None), install_ok=getattr(app, "install_ok", False),
                          icon_light_taskbar=bool(app.light_taskbar))
        # Sorting, translations, and autostart registry reads do not hold the
        # polling/tray lock. Only cached status and configuration are copied.
        from . import i18n
        for item in devices:
            item["state"] = ctx["device_state"](item["status"], item.pop("estimate"), language=cfg["language"])
        devices.sort(key=lambda row: row["name"].casefold())
        providers = [dict(name=name,
                         label=i18n.translate("provider." + name, language=cfg["language"]) if ctx["has_translation"]("provider." + name) else label,
                         enabled=name not in disabled) for name, label in ctx["provider_labels"].items()]
        providers.sort(key=lambda row: row["label"].casefold())
        result["providers"] = providers
        result["autostart"] = ctx["autostart_enabled"]()
        return result

    def set_icon_content(self, mode):
        if not isinstance(mode, str) or mode not in ("percent", "ring", "pictogram"):
            raise ValueError("invalid icon content mode")
        app = self.app
        with app.lock:
            app.cfg["percent_in_icon"] = mode == "percent"
            if mode != "percent":
                app.cfg["badges"] = mode == "pictogram"
            self._save()
        self._redraw()
        app.wake.set()

    def _save(self):
        self.context["save_config"](self.app.cfg)

    def _redraw(self):
        app = self.app
        with app.lock:
            for owner in list(app.icons.values()):
                if owner.status is not None:
                    owner.update(owner.status)
            if getattr(app, "placeholder", None) is not None:
                app.placeholder.icon = icons.render(None, False, False,
                    light_taskbar=app.light_taskbar, size=getattr(app, "tray_size", icons.SIZE),
                    ring_style=app.cfg.get("ring_style", "thin"))
        app.refresh_menus()

    def set_global(self, key, value):
        app, ctx = self.app, self.context
        if key == "autostart":
            if not isinstance(value, bool):
                raise ValueError("autostart must be boolean")
            if not ctx["set_autostart"](value):
                raise ValueError(app.tr("notification.temp_autostart"))
            return
        if key not in ctx["defaults"] or key == "disabled_providers" or not ctx["valid_setting"](key, value):
            raise ValueError("invalid setting: " + str(key))
        if key == "icon_theme" and value not in ("auto", "white", "black", "windows", "topbar"):
            raise ValueError("invalid icon theme")
        if key == "language":
            app.set_language(value)
            return
        if key == "filled_icons":
            app.set_icon_style(value)
            return
        with app.lock:
            app.cfg[key] = value
            if key == "bluetooth":
                app.bt_cache = []
                app.bt_wake.set()
            if key == "update_check":
                if value:
                    app.cfg["update_last"] = 0
                    app.update_wake.set()
                else:
                    app.update = None
            self._save()
            if key == "status_file" and not value:
                ctx["remove_status_file"]()
        if key == "icon_theme":
            app.refresh_theme()
            app.theme_evt.set()
        self._redraw()
        app.wake.set()

    def set_provider(self, name, enabled):
        if name not in self.context["provider_labels"] or not isinstance(enabled, bool):
            raise ValueError("invalid provider")
        if (name not in self.app.disabled_providers()) != enabled:
            self.app.toggle_provider(name)

    def set_device(self, key, field, value=None):
        app = self.app
        with app.lock:
            st = self._statuses().get(key)
        if st is None:
            return  # a device can disappear while its editor is open
        if field == "hidden":
            if not isinstance(value, bool):
                raise ValueError("hidden must be boolean")
            if value:
                app.hide(SimpleNamespace(status=st))
            else:
                app.unhide(key)
            return
        maps = {"name": "names", "reset_name": "names", "pictogram": "icons", "brand": "controller_brands",
                "brand_color": "controller_colors", "low_override": "lows"}
        if field not in maps:
            raise ValueError("invalid device setting")
        if field == "name":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("name cannot be empty")
            value = value.strip()[:60]
        if field == "pictogram" and (not isinstance(value, str) or value and value not in icons.PICTOGRAM_KINDS):
            raise ValueError("invalid pictogram")
        if field == "brand" and value not in self.context["controller_brands"]:
            raise ValueError("invalid controller brand")
        if field == "brand_color" and not isinstance(value, bool):
            raise ValueError("brand color must be boolean")
        if field == "low_override" and value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100):
            raise ValueError("invalid battery threshold")
        remove = (field == "reset_name" or field == "name" and value == st.name or
                  field == "pictogram" and value == "" or field == "brand" and value == "default" or
                  field == "brand_color" and not value or field == "low_override" and value is None)
        with app.lock:
            target = app._settings_map(maps[field])
            if remove:
                target.pop(key, None)
            else:
                target[key] = value
            if field == "pictogram":
                app._settings_map("custom_icons").pop(key, None)
                self._advance_icon_selection(key)
                self._prune_custom_assets()
            if field in ("name", "reset_name") and key in app._settings_map("hidden"):
                app._settings_map("hidden")[key] = app.display_name(st)
            if field == "low_override":
                app.alerted.pop(key, None)
                app.low_sound_at.pop(key, None)
            self._save()
        self._redraw()

    def preview(self, key=None, size=80, frame=None):
        if size != 80 or isinstance(size, bool):
            raise ValueError("Settings previews use exactly 80 physical pixels")
        app = self.app
        with app.lock:
            statuses = self._statuses()
            st = statuses.get(key) if key is not None else next(iter(statuses.values()), None)
            if st is None:
                return icons.render(None, False, False, light_taskbar=app.light_taskbar, size=size,
                                    ring_style=app.cfg.get("ring_style", "thin"))
            state = app.icon_render_state(st, size=size)
            tick = getattr(app, "anim_tick", 0) if frame is None else frame
            asset = getattr(app, "_custom_assets", {}).get(state[13]) if len(state) > 13 else None
        if state != self._preview_state:
            self._preview_state,self._preview_art=state,{}
        level,charging,online,low,light,badge,animate,text,paused,pixels,brand,color,filled=state[:13]
        custom = {"custom_glyph": asset.glyph} if asset is not None else {}
        if len(state) > 14:
            custom["ring_style"] = state[14]
        index=tick % icons.BREATH_FRAMES if animate else 0
        if index not in self._preview_art:
            # Match charging_frames exactly, but generate only the visible frame.
            # Eagerly rendering a whole 500% cycle stalls the settings UI when a
            # device first connects or its state changes.
            phase=index/icons.BREATH_FRAMES if animate else 0.
            self._preview_art[index]=icons.render(
                level,charging,online,low,light,badge,
                pulse=icons.breath_level(phase) if animate else 1.,text=text,
                force_active=paused if not animate else False,size=pixels,
                brand=brand,brand_color=color,filled=filled,
                animation_phase=phase if animate and level is None else 0., **custom)
        return self._preview_art[index].copy()

    def icon_catalog(self):
        # Independent from app.lock: registry/file/network work is never on a poll.
        with self._catalog_lock:
            if self._catalog is None:
                from .iconify_catalog import IconifyCatalog
                self._catalog = IconifyCatalog(self.context["data_dir"])
            return self._catalog

    def create_browse_catalog(self):
        # Each settings window owns independent disposable search/preview RAM.
        # Only applied assets share the persistent, offline tray asset store.
        from .iconify_catalog import IconifyCatalog
        return IconifyCatalog(self.context["data_dir"], transient=True)

    def custom_icon_in_use(self, icon_id):
        with self.app.lock:
            saved = self.app.cfg.get("custom_icons", {})
            ids = set(saved.values()) if isinstance(saved, dict) else set()
            return any(asset.id in ids and asset.icon_id == icon_id
                       for asset in getattr(self.app, "_custom_assets", {}).values())

    def custom_icon_ids(self):
        with self.app.lock:
            saved = self.app.cfg.get("custom_icons", {})
            return list(set(saved.values())) if isinstance(saved, dict) else []

    def load_custom_icons(self):
        self.app._custom_assets = {}
        ids = self.custom_icon_ids()
        if not ids and not (Path(self.context["data_dir"]) / "iconify").exists():
            return
        try:
            catalog = self.icon_catalog()
        except (OSError, ValueError):
            log.warning("settings: custom icon registry could not be loaded", exc_info=True)
            return
        for asset_id in ids:
            asset = catalog.load(asset_id)
            if asset is not None:
                self.app._custom_assets[asset.id] = asset
                try:
                    catalog.pin(asset.id)
                except (OSError, ValueError):
                    # A read-only portable profile can still display valid PNGs.
                    log.warning("settings: could not refresh custom icon retention", exc_info=True)

        # Migrate caches left by earlier builds before any window or upgrade
        # worker starts. Applied masters have already been loaded and retained.
        try:
            catalog.purge_unselected_assets(ids)
            catalog.clear_browse_cache(ids)
        except (OSError, ValueError):
            log.warning("settings: old temporary icon cache could not be cleared", exc_info=True)

    def _advance_icon_selection(self, key):
        revision = self._icon_selection_revisions.get(key, 0) + 1
        self._icon_selection_revisions[key] = revision
        return revision

    def begin_custom_selection(self, key):
        with self.app.lock:
            return self._advance_icon_selection(key)

    def _prune_custom_assets(self):
        saved = self.app.cfg.get("custom_icons", {})
        used = set(saved.values()) if isinstance(saved, dict) else set()
        assets = getattr(self.app, "_custom_assets", {})
        self.app._custom_assets = {key: asset for key, asset in assets.items() if key in used}

    def apply_custom_icon(self, key, asset, *, revision=None):
        app = self.app
        with app.lock:
            if key not in self._statuses() or revision is not None and self._icon_selection_revisions.get(key) != revision:
                return False
        if asset is not None:
            self.icon_catalog().pin(asset.id)
        with app.lock:
            # Pinning may perform disk IO; a newer selection wins during it.
            if key not in self._statuses() or revision is not None and self._icon_selection_revisions.get(key) != revision:
                return False
            if revision is None:
                self._advance_icon_selection(key)
            target = app._settings_map("custom_icons")
            if asset is None:
                target.pop(key, None)
            else:
                if not hasattr(app, "_custom_assets"):
                    app._custom_assets = {}
                app._custom_assets[asset.id] = asset
                target[key] = asset.id
            self._prune_custom_assets()
            self._save()
        self._redraw()
        return True

    def adopt_custom_icon_upgrade(self, asset, *, expected=None):
        """Improve the saved representation without changing a user's choice."""
        catalog, app = self.icon_catalog(), self.app
        if catalog.needs_upgrade(asset):
            return False
        with app.lock:
            target = app._settings_map("custom_icons")
            matches = {}
            for key, old_id in target.items():
                old = getattr(app, "_custom_assets", {}).get(old_id)
                revision = self._icon_selection_revisions.get(key, 0)
                if (old is not None and old_id != asset.id and old.icon_id == asset.icon_id
                        and old.metadata.get("source_hash") == asset.metadata.get("source_hash")
                        and (expected is None or expected.get(key) == (old_id, revision))):
                    matches[key] = (old_id, revision)
        if not matches:
            return False
        catalog.pin(asset.id)
        with app.lock:
            target = app._settings_map("custom_icons")
            if getattr(app, "stop_evt", None) is not None and app.stop_evt.is_set():
                return False
            changed = False
            for key, (old_id, revision) in matches.items():
                if (target.get(key) == old_id and self._icon_selection_revisions.get(key, 0) == revision):
                    app._custom_assets[asset.id] = asset
                    target[key] = asset.id
                    changed = True
            if changed:
                self._prune_custom_assets()
                self._save()
        if changed:
            self._redraw()
        return changed

    def schedule_custom_icon_upgrade(self):
        """One background migration for old masks; ordinary startup stays local."""
        if self._icon_upgrade_started:
            return
        self._icon_upgrade_started = True
        catalog = self._catalog
        if catalog is None:
            return
        app = self.app
        with app.lock:
            pending = {}
            for key, asset_id in app._settings_map("custom_icons").items():
                asset = getattr(app, "_custom_assets", {}).get(asset_id)
                if asset is not None and catalog.needs_upgrade(asset):
                    pending.setdefault(asset_id, (asset, {}))[1][key] = (
                        asset_id, self._icon_selection_revisions.get(key, 0))
        if not pending:
            return
        cancel = getattr(app, "stop_evt", None)
        def upgrade():
            for asset, bindings in pending.values():
                if cancel is not None and cancel.is_set():
                    return
                try:
                    prepared = catalog.prepare(asset.icon_id, cancel=cancel)
                    self.adopt_custom_icon_upgrade(prepared, expected=bindings)
                except (OSError, ValueError):
                    log.warning("settings: deferred custom icon upgrade unavailable", exc_info=True)
        threading.Thread(target=upgrade, daemon=True, name="iconify-cache-upgrade").start()

    def catalog_render_state(self, key):
        with self.app.lock:
            st = self._statuses().get(key)
            if st is None:
                raise ValueError("Device no longer available")
            return self.app.icon_render_state(st), max(8, min(192, getattr(self.app, "tray_size", 16)))

    def action(self, name):
        app = self.app
        actions = dict(refresh=app.wake.set, diagnostics=app.request_diag, check_update=app.check_update_now,
                       start_update=app.start_update, quit=app.quit,
                       open_data=lambda: os.startfile(self.context["data_dir"]),
                       open_project=lambda: app.open_url("https://github.com/" + self.context["repo"]),
                       open_issue=lambda: app.open_url("https://github.com/" + self.context["repo"] + "/issues/new/choose"))
        if name not in actions:
            raise ValueError("unknown settings action")
        return actions[name]()
