#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const PTT_SHORTCUT: &str = "Ctrl+Shift+Space";

fn ptt_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space)
}

fn tray_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let open_cockpit = MenuItem::with_id(app, "open_cockpit", "Open Cockpit", true, None::<&str>)?;
    let voice_ptt = MenuItem::with_id(app, "voice_ptt", "Toggle Voice (Ctrl+Shift+Space)", true, None::<&str>)?;
    let morning = MenuItem::with_id(app, "morning_brief", "Run Morning Brief", true, None::<&str>)?;
    let doctor = MenuItem::with_id(app, "doctor", "Machine Doctor", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    Menu::with_items(
        app,
        &[
            &open_cockpit,
            &voice_ptt,
            &morning,
            &PredefinedMenuItem::separator(app)?,
            &doctor,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )
}

fn handle_tray_event(_app: &AppHandle, event: TrayIconEvent) {
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        ..
    } = event
    {
        let _ = sidecar::open_dashboard();
        tracing::info!("tray click -> open cockpit");
    }
}

fn handle_menu_event(app: &AppHandle, id: &str) {
    match id {
        "open_cockpit" => {
            if let Err(err) = sidecar::open_dashboard() {
                tracing::warn!(error = %err, "open cockpit failed");
            }
        }
        "voice_ptt" => match sidecar::toggle_voice_loop() {
            Ok(msg) => tracing::info!(msg = %msg, "voice toggle"),
            Err(err) => tracing::warn!(error = %err, "voice toggle failed"),
        },
        "morning_brief" => {
            if let Err(err) = sidecar::run_morning_brief() {
                tracing::warn!(error = %err, "morning brief failed");
            }
        }
        "doctor" => match sidecar::run_doctor() {
            Ok(out) => tracing::info!(doctor = %out, "doctor ok"),
            Err(err) => tracing::warn!(error = %err, "doctor failed"),
        },
        "quit" => {
            sidecar::stop_voice_loop();
            app.exit(0);
        }
        _ => {}
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--hidden"]),
        ))
        .setup(|app| {
            tracing_subscriber::fmt()
                .with_env_filter(
                    tracing_subscriber::EnvFilter::try_from_default_env()
                        .unwrap_or_else(|_| "info".into()),
                )
                .init();

            let menu = tray_menu(app.handle())?;
            let _tray = TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| handle_menu_event(app, event.id().as_ref()))
                .on_tray_icon_event(|tray, event| handle_tray_event(tray.app_handle(), event))
                .build(app)?;

            app.handle().plugin(
                tauri_plugin_global_shortcut::Builder::new()
                    .with_handler(move |_app, _shortcut, event| {
                        if event.state == ShortcutState::Pressed {
                            match sidecar::toggle_voice_loop() {
                                Ok(msg) => tracing::info!(msg = %msg, "PTT hotkey"),
                                Err(err) => tracing::warn!(error = %err, "PTT hotkey failed"),
                            }
                        }
                    })
                    .build(),
            )?;

            let shortcut = ptt_shortcut();
            if let Err(e) = app.handle().global_shortcut().register(shortcut) {
                tracing::warn!(error = %e, shortcut = PTT_SHORTCUT, "PTT registration failed");
            }

            match sidecar::health_json() {
                Ok(health) => tracing::info!(sidecar.health = %health, "sidecar health ok"),
                Err(err) => tracing::warn!(sidecar.error = %err, "sidecar health unavailable"),
            }

            let _ = sidecar::open_dashboard();
            tracing::info!("starlight-voice tray ready; PTT={PTT_SHORTCUT}");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}