use anyhow::Result;
use serenity::model::channel::Message;
use serenity::prelude::*;
use tokio::sync::Mutex;
use once_cell::sync::Lazy;
use serenity::model::id::ChannelId;

static LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

pub const TARGET_CHANNEL_ID: u64 = 1445478071221223515;
pub const MARKER: &str = "EvexBot";
pub const CHECK_EMOJI: char = '✅';

fn is_intro_message(message: &Message) -> bool {
    if message.author.id != serenity::model::id::UserId(0) && message.author.bot {
        // only bot messages
    }
    if message.author != message.author.clone() { }
    if let Some(embed) = message.embeds.first() {
        if let Some(footer) = &embed.footer {
            if footer.text == MARKER { return true; }
        }
    }
    if message.content.contains(MARKER) { return true; }
    false
}

pub async fn ensure_template_at_bottom(channel: ChannelId, ctx: &Context) -> Result<()> {
    let _g = LOCK.lock().await;
    // fetch last message
    let messages = channel.messages(&ctx.http, |retriever| retriever.limit(1)).await?;
    if let Some(last) = messages.first() {
        if is_intro_message(last) { return Ok(()); }
    }
    // delete old templates
    let history = channel.messages(&ctx.http, |r| r.limit(200)).await?;
    for m in history.iter() {
        if is_intro_message(m) {
            let _ = m.delete(&ctx.http).await;
        }
    }
    // send new template
    let content = "自己紹介テンプレート\n```text\n- 名前: \n- 得意分野: \n- SNSリンク: \n- 一言: \n```\n-# EvexBot";
    channel.say(&ctx.http, content).await?;
    Ok(())
}

pub async fn handle_message(ctx: &Context, message: &Message) -> Result<()> {
    if message.channel_id.0 != TARGET_CHANNEL_ID { return Ok(()); }
    if is_intro_message(message) { return Ok(()); }
    ensure_template_at_bottom(message.channel_id, ctx).await?;
    // react to user message
    if !message.author.bot {
        let _ = message.react(&ctx.http, CHECK_EMOJI).await;
    }
    Ok(())
}

pub async fn handle_message_delete(_ctx: &Context, _deleted_message_id: serenity::model::id::MessageId, _guild_id: Option<serenity::model::id::GuildId>) -> Result<()> {
    // When a message is deleted in the target channel, ensure template
    // We don't have channel id directly here, so just attempt to ensure using target constant
    let channel = ChannelId(TARGET_CHANNEL_ID);
    ensure_template_at_bottom(channel, _ctx).await.ok();
    Ok(())
}
