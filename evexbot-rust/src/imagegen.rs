use anyhow::Result;
use regex::Regex;
use serenity::model::application::interaction::application_command::ApplicationCommandInteraction;
use serenity::prelude::*;
use std::time::Duration;
use reqwest::Client;


const API_BASE_URL: &str = "https://image-ai.evex.land";
const MAX_PROMPT_LENGTH: usize = 1000;

fn validate_prompt(prompt: &str) -> Result<(), String> {
    if prompt.len() > MAX_PROMPT_LENGTH { return Err(format!("プロンプトは{}文字以内で指定してください。", MAX_PROMPT_LENGTH)); }
    let invalid_patterns = [r"[<>{}\\[\\]\\\\]", r"(?:https?://|www\.)\S+"];
    for pat in invalid_patterns.iter() {
        let re = Regex::new(pat).unwrap();
        if re.is_match(prompt) { return Err("プロンプトに不適切な文字が含まれています。".to_string()); }
    }
    Ok(())
}

pub async fn handle_imagegen(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;
    let prompt = command.data.options.get(0).and_then(|o| o.value.as_ref()).and_then(|v| v.as_str()).unwrap_or("");
    if let Err(err) = validate_prompt(prompt) { command.create_followup_message(&ctx.http, |m| m.content(err)).await?; return Ok(()); }

    let client = Client::builder().timeout(Duration::from_secs(30)).build()?;
    let resp = client.get(format!("{}/?prompt={}", API_BASE_URL, urlencoding::encode(prompt))).send().await;
    match resp {
        Ok(r) => {
            if r.status().is_success() {
                let bytes = r.bytes().await?;
                let mut embed = serenity::builder::CreateEmbed::default();
                embed.title("生成された画像");
                embed.description(format!("プロンプト: {}", prompt));
                embed.image("attachment://generated_image.png");
                embed.footer(|f| f.text("API Powered by Evex"));
                command.create_followup_message(&ctx.http, |m| m.add_file((bytes.as_ref(), "generated_image.png")).embed(|e| { *e = embed; e })).await?;
            } else {
                command.create_followup_message(&ctx.http, |m| m.content("画像の生成に失敗しました。時間をおいて再度お試しください。" )).await?;
            }
        }
        Err(e) => {
            command.create_followup_message(&ctx.http, |m| m.content(format!("APIエラーが発生しました: {}", e))).await?;
        }
    }
    Ok(())
}
