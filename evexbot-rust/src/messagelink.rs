use anyhow::Result;
use regex::Regex;
use serenity::model::channel::Message;
use serenity::prelude::*;
use serenity::model::prelude::component::ButtonStyle;

pub async fn handle_message(ctx: &Context, message: &Message) -> Result<()> {
    // ignore bot's own messages
    if message.author.bot { return Ok(()); }

    let privacy = false; // Privacy cog not implemented in Rust; assume absent

    if privacy { return Ok(()); }

    let re = Regex::new(r"https://(?:canary\.|ptb\.)?discord\.com/channels/(\d+)/(\d+)/(\d+)")?;
    if let Some(cap) = re.captures(&message.content) {
        let guild_id: u64 = cap.get(1).unwrap().as_str().parse()?;
        let channel_id: u64 = cap.get(2).unwrap().as_str().parse()?;
        let message_id: u64 = cap.get(3).unwrap().as_str().parse()?;

        // fetch guild and channel
        let channel = serenity::model::id::ChannelId(channel_id);
        // check nsfw
        if let Ok(ch) = channel.to_channel(&ctx.http).await {
            if ch.is_nsfw() { return Ok(()); }
        }

        if let Ok(target) = channel.message(&ctx.http, message_id).await {
            message.channel_id.send_message(&ctx.http, |m| {
                m.embed(|e| {
                    e.description(&target.content);
                    e.color(serenity::utils::Colour::BLUE);
                    e.author(|a| a.name(&target.author.name).icon_url(target.author.avatar_url().unwrap_or_default()));
                    let ts_str = target.timestamp.to_string();
                    e.footer(|f| f.text(format!("Sent on {} in {}", ts_str, message.guild_id.map(|g| g.0.to_string()).unwrap_or_default())));
                    e
                });
                m.components(|c| c.create_action_row(|ar| {
                    ar.create_button(|b| b.custom_id("delete_embed_button").label("削除").style(ButtonStyle::Danger))
                }));
                m
            }).await?;
        }
    }
    Ok(())
}
