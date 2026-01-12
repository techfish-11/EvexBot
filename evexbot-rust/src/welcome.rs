use anyhow::Result;
use chrono::Utc;
use plotters::prelude::*;
use serenity::builder::CreateEmbed;
use serenity::http::Http;
use serenity::model::application::interaction::InteractionResponseType;
use serenity::model::gateway::Ready;
use serenity::model::id::{GuildId, ChannelId, UserId};
use serenity::model::prelude::*;
use serenity::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

use crate::db;
use crate::growth;

static LAST_WELCOME: once_cell::sync::Lazy<Arc<Mutex<HashMap<i64, chrono::DateTime<chrono::Utc>>>>> = once_cell::sync::Lazy::new(|| Arc::new(Mutex::new(HashMap::new())));

pub const ROLE_ID: u64 = 1255803402898898964;
const JOIN_COOLDOWN_SECONDS: i64 = 3;

pub async fn handle_member_join(ctx: &Context, new_member: Member) -> Result<()> {
    if new_member.user.bot {
        return Ok(());
    }

    let guild_id = new_member.guild_id.0 as i64;

    let (is_enabled, increment, channel_id_opt) = db::get_welcome_settings(guild_id).await?;
    if !is_enabled {
        return Ok(());
    }

    // Cooldown
    {
        let last_welcome = LAST_WELCOME.clone();
        let mut lock = last_welcome.lock().await;
        if let Some(last) = lock.get(&guild_id) {
            if (Utc::now() - *last).num_seconds() < JOIN_COOLDOWN_SECONDS {
                return Ok(());
            }
        }
        lock.insert(guild_id, Utc::now());
    }

    let channel_id = match channel_id_opt {
        Some(id) => ChannelId(id as u64),
        None => {
            // disable
            db::update_welcome_settings(guild_id, false, None, None).await.ok();
            return Ok(());
        }
    };

    // Fetch member count
    let member_count = {
        let members = ctx.http.get_guild_members(new_member.guild_id.0, None, None).await?;
        members.len() as i64
    };

    let remainder = member_count % increment;
    let (is_milestone, next_target) = if remainder == 0 {
        (true, member_count + increment)
    } else {
        (false, member_count + (increment - remainder))
    };

    // Fetch join dates
    let join_dates = fetch_all_join_dates(ctx, new_member.guild_id).await?;

    if is_milestone {
        // Generate graph
        if let Some(buf) = create_growth_graph(&join_dates, member_count).await? {
            // send embed with image
            let mut embed = CreateEmbed::default();
            embed.title("🎉 Welcome EvexDevelopers! 🎉");
            let guild_name = ctx.cache.guild(new_member.guild_id.0).map(|g| g.name.clone()).unwrap_or_else(|| "Server".to_string());
            embed.description(format!("{} さん、ようこそ！\n現在のメンバー数: **{}人**\n{}のメンバーが{}人になりました！皆さんありがとうございます！\n良ければ、<#1445478071221223515>で自己紹介お願いします！。", new_member.user.mention(), member_count, guild_name, member_count));
            embed.color(serenity::utils::Colour::GOLD);
            embed.timestamp(Utc::now().to_rfc3339());
            embed.footer(|f| f.text("EvexBot | Member Growth"));

            // send using byte slice tuple expected by serenity add_file/send_files
            channel_id.send_files(&ctx.http, vec![(buf.as_slice(), "growth.png")], |m| m.embed(|e| { *e = embed; e })).await?;

            // spawn prediction task to compute when next_target is reached and edit message
            let http = ctx.http.clone();
            let ch = channel_id;
            let join_dates_clone = join_dates.clone();
            tokio::spawn(async move {
                if let Ok(Some((target_date, _img))) = growth::predict_and_generate(&join_dates_clone, next_target as usize).await {
                    let content = format!("次の目標到達予測: {}人: {}", next_target, target_date.date_naive());
                    let _ = ch.say(&http, content).await;
                }
            });
        }
    } else {
        let content = format!("{} さん、ようこそ！\n現在のメンバー数: {}人\nあと {} 人で {}人達成です！\n良ければ、<#1445478071221223515>で自己紹介お願いします！。", new_member.user.mention(), member_count, increment - remainder, next_target);
        let sent = channel_id.say(&ctx.http, content).await?;

        // spawn prediction background task that edits the message
        let http = ctx.http.clone();
        let mut sent_clone = sent.clone();
        let join_dates_clone = join_dates.clone();
        tokio::spawn(async move {
            if let Ok(pred) = growth::predict_and_generate(&join_dates_clone, next_target as usize).await {
                if let Some((target_date, _img)) = pred {
                    let days = (target_date.date_naive() - chrono::Utc::now().date_naive()).num_days();
                    let edit_content = format!("{}\n次の目標到達予測: {}人: {} (あと{}日)", sent_clone.content, next_target, target_date.date_naive(), days);
                    let _ = sent_clone.edit(&http, |b| b.content(edit_content)).await;
                }
            }
        });
    }

    Ok(())
}

async fn fetch_all_join_dates(ctx: &Context, guild_id: GuildId) -> Result<Vec<chrono::NaiveDateTime>> {
    let mut dates = Vec::new();
    let members = ctx.http.get_guild_members(guild_id.0, None, None).await?;
    for m in members.into_iter() {
        if let Some(joined_at) = m.joined_at {
            if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(&joined_at.to_string()) {
                dates.push(dt.naive_utc());
            }
        }
    }
    dates.sort();
    Ok(dates)
}

async fn create_growth_graph(dates: &Vec<chrono::NaiveDateTime>, achieved_count: i64) -> Result<Option<Vec<u8>>> {
    if dates.is_empty() { return Ok(None); }
    use plotters_bitmap::BitMapBackend;

    let min_date = dates[0].date();
    let max_date = dates.last().unwrap().date();
    let days = (max_date - min_date).num_days() as usize + 1;
    let mut counts = vec![0i32; days];
    for dt in dates.iter() {
        let idx = (dt.date() - min_date).num_days() as usize;
        for i in idx..days { counts[i] += 1; }
    }

    let width = 800;
    let height = 300;
    let mut buf: Vec<u8> = vec![0; width * height * 3];

    let date_labels: Vec<chrono::NaiveDate> = (0..days).map(|i| min_date + chrono::Duration::days(i as i64)).collect();
    let max_count = *counts.iter().max().unwrap_or(&0) as i32 + 2;

    {
        let backend = BitMapBackend::with_buffer(&mut buf, (width as u32, height as u32));
        let drawing_area = backend.into_drawing_area();
        drawing_area.fill(&WHITE)?;

        let mut chart = ChartBuilder::on(&drawing_area)
            .margin(10)
            .caption("Member Growth History", ("sans-serif", 20))
            .x_label_area_size(35)
            .y_label_area_size(40)
            .build_cartesian_2d(0usize..days, 0i32..max_count)?;
        chart.configure_mesh().disable_mesh().x_labels(6).x_label_formatter(&|v| date_labels[*v].to_string()).draw()?;

        chart.draw_series(LineSeries::new(
            (0..days).map(|i| (i, counts[i])),
            &BLUE,
        ))?;

        drawing_area.present()?;
    }

    // Convert RGB buffer to PNG
    let image = image::RgbImage::from_raw(width as u32, height as u32, buf).ok_or_else(|| anyhow::anyhow!("Failed to create image"))?;
    let mut out = Vec::new();
    image::DynamicImage::ImageRgb8(image).write_to(&mut std::io::Cursor::new(&mut out), image::ImageOutputFormat::Png)?;
    Ok(Some(out))
}

pub async fn handle_member_remove(ctx: &Context, guild_id: GuildId, user_id: UserId) -> Result<()> {
    let _guild = guild_id.to_guild_cached(&ctx.cache).ok_or_else(|| anyhow::anyhow!("Guild not in cache"))?;
    let guild_id = guild_id.0 as i64;
    let (is_enabled, channel_id_opt) = db::get_leave_settings(guild_id).await?;
    if !is_enabled {
        return Ok(());
    }
    let channel_id = match channel_id_opt { Some(id) => ChannelId(id as u64), None => { db::update_leave_settings(guild_id, false, None).await.ok(); return Ok(()); } };

    // Compute member_count
    let members = ctx.http.get_guild_members(GuildId(guild_id as u64).0, None, None).await?;
    let member_count = members.len();

    let message = format!("<@{}> さんがサーバーを退室しました。\n現在のメンバー数: {}人", user_id.0, member_count);
    channel_id.say(&ctx.http, message).await?;
    Ok(())
}

pub async fn register_commands(http: &Http) -> Result<()> {
    // Register /welcome and /leave-message and /milestonetest
    let _ = serenity::model::application::command::Command::create_global_application_command(http, |c| {
        c.name("welcome").description("参加メッセージの設定").create_option(|o| {
            o.name("action").description("enable|disable").kind(serenity::model::application::command::CommandOptionType::String).required(true)
        }).create_option(|o| {
            o.name("increment").description("何人ごとにお祝い").kind(serenity::model::application::command::CommandOptionType::Integer).required(false)
        }).create_option(|o| {
            o.name("channel").description("送信先チャンネル").kind(serenity::model::application::command::CommandOptionType::Channel).required(false)
        })
    }).await;

    let _ = serenity::model::application::command::Command::create_global_application_command(http, |c| {
        c.name("leave-message").description("退室メッセージの設定").create_option(|o| {
            o.name("action").description("enable|disable").kind(serenity::model::application::command::CommandOptionType::String).required(true)
        }).create_option(|o| {
            o.name("channel").description("送信先チャンネル").kind(serenity::model::application::command::CommandOptionType::Channel).required(false)
        })
    }).await;

    let _ = serenity::model::application::command::Command::create_global_application_command(http, |c| {
        c.name("milestonetest").description("管理者用: マイルストーンテスト")
    }).await;

    Ok(())
}

use serenity::model::application::interaction::application_command::ApplicationCommandInteraction;

pub async fn handle_welcome_command(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;
    let action = command.data.options.get(0).and_then(|o| o.value.as_ref()).and_then(|v| v.as_str()).unwrap_or("");
    let increment = command.data.options.iter().find(|o| o.name=="increment").and_then(|o| o.value.as_ref()).and_then(|v| v.as_i64()).map(|v| v as i64);
    let channel = command.data.options.iter().find(|o| o.name=="channel").and_then(|o| o.resolved.as_ref()).and_then(|r| match r { serenity::model::prelude::application_command::CommandDataOptionValue::Channel(c) => Some(c.clone()), _ => None });

    // role check
    let member = command.member.as_ref().ok_or_else(|| anyhow::anyhow!("member required"))?;
    if !member.roles.iter().any(|r| r.0 == ROLE_ID) { command.create_followup_message(&ctx.http, |m| m.content("コマンドを使用するにはサーバーの管理権限が必要です。" ).ephemeral(true)).await?; return Ok(()); }

    match action {
        "enable" => {
            if channel.is_none() { command.create_followup_message(&ctx.http, |m| m.content("ONにする場合はチャンネルを指定してください。" ).ephemeral(true)).await?; return Ok(()); }
            let chan_id = if let Some(c) = channel { c.id.0 as i64 } else { 0 };
            let inc = increment.unwrap_or(100);
            if inc < 5 || inc > 1000 { command.create_followup_message(&ctx.http, |m| m.content("5～1000人の間で指定してください。" ).ephemeral(true)).await?; return Ok(()); }
            db::update_welcome_settings(command.guild_id.ok_or_else(|| anyhow::anyhow!("guild required"))?.0 as i64, true, Some(inc), Some(chan_id)).await?;
            command.create_followup_message(&ctx.http, |m| m.content(format!("参加メッセージをONにしました!\n{}人ごとに<#{}>でお祝いメッセージを送信します", inc, chan_id)).ephemeral(true)).await?;
        }
        "disable" => {
            db::update_welcome_settings(command.guild_id.ok_or_else(|| anyhow::anyhow!("guild required"))?.0 as i64, false, None, None).await?;
            command.create_followup_message(&ctx.http, |m| m.content("参加メッセージを無効にしました!").ephemeral(true)).await?;
        }
        _ => { command.create_followup_message(&ctx.http, |m| m.content("enableまたはdisableを指定してください。" ).ephemeral(true)).await?; }
    }
    Ok(())
}

pub async fn handle_leave_command(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;
    let action = command.data.options.get(0).and_then(|o| o.value.as_ref()).and_then(|v| v.as_str()).unwrap_or("");
    let channel = command.data.options.iter().find(|o| o.name=="channel").and_then(|o| o.resolved.as_ref()).and_then(|r| match r { serenity::model::prelude::application_command::CommandDataOptionValue::Channel(c) => Some(c.clone()), _ => None });

    let member = command.member.as_ref().ok_or_else(|| anyhow::anyhow!("member required"))?;
    if !member.roles.iter().any(|r| r.0 == ROLE_ID) { command.create_followup_message(&ctx.http, |m| m.content("コマンドを使用するにはサーバーの管理権限が必要です。" ).ephemeral(true)).await?; return Ok(()); }

    match action {
        "enable" => {
            if channel.is_none() { command.create_followup_message(&ctx.http, |m| m.content("ONにする場合はチャンネルを指定してください。" ).ephemeral(true)).await?; return Ok(()); }
            let chan_id = if let Some(c) = channel { c.id.0 as i64 } else { 0 };
            db::update_leave_settings(command.guild_id.ok_or_else(|| anyhow::anyhow!("guild required"))?.0 as i64, true, Some(chan_id)).await?;
            command.create_followup_message(&ctx.http, |m| m.content(format!("退室メッセージをONにしました! チャンネル: <#{}>", chan_id)).ephemeral(true)).await?;
        }
        "disable" => {
            db::update_leave_settings(command.guild_id.ok_or_else(|| anyhow::anyhow!("guild required"))?.0 as i64, false, None).await?;
            command.create_followup_message(&ctx.http, |m| m.content("退室メッセージを無効にしました!").ephemeral(true)).await?;
        }
        _ => { command.create_followup_message(&ctx.http, |m| m.content("enableまたはdisableを指定してください。" ).ephemeral(true)).await?; }
    }
    Ok(())
}

pub async fn handle_milestone_test(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;
    // permission check by user id
    if command.user.id.0 != 1241397634095120438u64 { command.create_followup_message(&ctx.http, |m| m.content("権限がありません。" )).await?; return Ok(()); }

    let guild = command.guild_id.ok_or_else(|| anyhow::anyhow!("Guild only"))?;
    let join_dates = fetch_all_join_dates(&ctx, guild).await?;
    let member_count = ctx.http.get_guild_members(guild.0, None, None).await?.len();
    let next_target = member_count as i64 + 100;

    // generate graph
    if let Some(buf) = create_growth_graph(&join_dates, member_count as i64).await? {
        let mut embed = serenity::builder::CreateEmbed::default();
        embed.title("🎉 Welcome EvexDevelopers! 🎉");
        let guild_name = command.guild_id.and_then(|gid| ctx.cache.guild(gid.0).map(|g| g.name.clone())).unwrap_or_else(|| "Server".to_string());
        embed.description(format!("{} さん、ようこそ！\n現在のメンバー数: **{}人**\n{}のメンバーが{}人になりました！皆さんありがとうございます！", command.user.mention(), member_count, guild_name, member_count));
        embed.color(serenity::utils::Colour::GOLD);
        embed.timestamp(chrono::Utc::now().to_rfc3339());
        embed.footer(|f| f.text("EvexBot | Member Growth"));
        command.create_followup_message(&ctx.http, |m| m.add_file((buf.as_slice(), "growth.png")).embed(|e| { *e = embed; e })).await?;

        let join_dates_clone = join_dates.clone();
        let cmd_clone = command.clone();
        let http = ctx.http.clone();
        tokio::spawn(async move {
            if let Ok(Some((target_date, _))) = crate::growth::predict_and_generate(&join_dates_clone, next_target as usize).await {
                let days = (target_date.date_naive() - chrono::Utc::now().date_naive()).num_days();
                let _ = cmd_clone.create_followup_message(&http, |m| m.content(format!("次の目標到達予測: {}人: {} (あと{}日)", next_target, target_date.date_naive(), days))).await;
            }
        });
    }
    Ok(())
}
