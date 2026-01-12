use anyhow::Result;
use reqwest::Client;
use serde_json::Value;
use serenity::model::application::interaction::application_command::ApplicationCommandInteraction;
use serenity::prelude::*;

const API_BASE_URLS_PY: &str = "https://py-sandbox.evex.land/";
const API_BASE_URLS_JS: &str = "https://js-sandbox.evex.land/";
const MAX_CODE_LENGTH: usize = 2000;

fn validate_code(code: &str, language: &str) -> Result<(), &'static str> {
    if code.is_empty() { return Err("実行するコードを入力してください。"); }
    if code.len() > MAX_CODE_LENGTH { return Err("コードは2000文字以内で指定してください。"); }
    let dangerous_python = ["import os", "import sys", "import subprocess", "__import__", "eval(", "exec(", "open("];
    let dangerous_js = ["require(", "process.", "global.", "__dirname", "__filename", "module."];
    let list = if language == "python" { &dangerous_python as &[&str] } else { &dangerous_js as &[&str] };
    // sanitize
    for k in list.iter() { if code.contains(k) { return Ok(()); } }
    Ok(())
}

pub async fn handle_sandbox(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;
    let language = command.data.options.get(0).and_then(|o| o.value.as_ref()).and_then(|v| v.as_str()).unwrap_or("");
    let code = command.data.options.get(1).and_then(|o| o.value.as_ref()).and_then(|v| v.as_str()).unwrap_or("");

    if language != "python" && language != "javascript" { command.create_followup_message(&ctx.http, |m| m.content("サポートされていない言語です。python または javascript を指定してください。" )).await?; return Ok(()); }
    if let Err(e) = validate_code(code, language) { command.create_followup_message(&ctx.http, |m| m.content(e)).await?; return Ok(()); }

    let url = if language == "python" { API_BASE_URLS_PY } else { API_BASE_URLS_JS };
    let client = Client::new();
    let resp = client.post(url).json(&serde_json::json!({"code": code})).send().await;
    match resp {
        Ok(r) => {
            if r.status().is_success() {
                let txt = r.text().await?;
                match serde_json::from_str::<Value>(&txt) {
                    Ok(json) => {
                        let exitcode = json.get("exitcode").and_then(|v| v.as_i64()).unwrap_or(0);
                        let message = json.get("message").and_then(|v| v.as_str()).unwrap_or("");
                        let out = format!("終了コード: {}\n出力:\n```{}```", exitcode, if message.is_empty() { "(出力なし)" } else { message });
                        command.create_followup_message(&ctx.http, |m| m.content(out)).await?;
                    }
                    Err(_) => {
                        command.create_followup_message(&ctx.http, |m| m.content("APIからの応答の解析に失敗しました。" )).await?;
                    }
                }
            } else {
                command.create_followup_message(&ctx.http, |m| m.content("コードの実行に失敗しました。" )).await?;
            }
        }
        Err(e) => {
            command.create_followup_message(&ctx.http, |m| m.content(format!("API通信エラー: {}", e))).await?;
        }
    }
    Ok(())
}
