use anyhow::Result;
use serenity::async_trait;
use serenity::prelude::GatewayIntents;
use serenity::model::gateway::Ready;
use serenity::prelude::*;
use std::env;

mod config;
mod db;
mod welcome;
mod growth;
mod imagegen;
mod avatar;
mod messagelink;
mod members_history;
mod sandbox;
mod zikosyokai;

struct Handler;

#[async_trait]
impl EventHandler for Handler {
    async fn ready(&self, ctx: Context, ready: Ready) {
        println!("Logged in as {}", ready.user.name);

        // Register a minimal set of global application commands used by the bot.
        let _ = serenity::model::application::command::Command::create_global_application_command(&ctx.http, |c| {
            c.name("growth").description("サーバーの成長を予測します。使用法: /growth model target show_graph:true/false").create_option(|o| o.name("model").description("polynomial|prophet").kind(serenity::model::application::command::CommandOptionType::String).required(true)).create_option(|o| o.name("target").description("目標とするメンバー数").kind(serenity::model::application::command::CommandOptionType::Integer).required(true)).create_option(|o| o.name("show_graph").description("グラフを表示するかどうか").kind(serenity::model::application::command::CommandOptionType::Boolean).required(false))
        }).await;

        let _ = serenity::model::application::command::Command::create_global_application_command(&ctx.http, |c| {
            c.name("members-history").description("指定した日付範囲のメンバー数推移をグラフ化します。")
                .create_option(|o| {
                    o.name("start_date").description("開始日 (YYYY-MM-DD)").kind(serenity::model::application::command::CommandOptionType::String).required(true)
                })
                .create_option(|o| {
                    o.name("end_date").description("終了日 (YYYY-MM-DD)").kind(serenity::model::application::command::CommandOptionType::String).required(true)
                })
        }).await;

        // Additional command registration performed by modules
        let _ = welcome::register_commands(&ctx.http).await;
        let _ = serenity::model::application::command::Command::create_global_application_command(&ctx.http, |c| {
            c.name("imagegen").description("与えられたプロンプトに基づいて画像を生成します").create_option(|o| o.name("prompt").description("生成する画像の説明（プロンプト）").kind(serenity::model::application::command::CommandOptionType::String).required(true))
        }).await;
        let _ = serenity::model::application::command::Command::create_global_application_command(&ctx.http, |c| {
            c.name("avatar").description("ユーザーのアイコンを表示します").create_option(|o| o.name("user").description("対象ユーザー").kind(serenity::model::application::command::CommandOptionType::User).required(false))
        }).await;
        let _ = serenity::model::application::command::Command::create_global_application_command(&ctx.http, |c| {
            c.name("sandbox").description("コードをサンドボックスで実行し、結果を返します。").create_option(|o| o.name("language").description("言語: python|javascript").kind(serenity::model::application::command::CommandOptionType::String).required(true)).create_option(|o| o.name("code").description("実行するコード").kind(serenity::model::application::command::CommandOptionType::String).required(true))
        }).await;
    }

    async fn interaction_create(&self, ctx: Context, interaction: serenity::model::interactions::Interaction) {
        match interaction {
            serenity::model::interactions::Interaction::ApplicationCommand(command) => {
                match command.data.name.as_str() {
                    "growth" => { let _ = growth::handle_growth(&ctx, &command).await; }
                    "members-history" => { let _ = members_history::handle_members_history(&ctx, &command).await; }
                    "imagegen" => { let _ = imagegen::handle_imagegen(&ctx, &command).await; }
                    "avatar" => { let _ = avatar::handle_avatar(&ctx, &command).await; }
                    "sandbox" => { let _ = sandbox::handle_sandbox(&ctx, &command).await; }
                    // welcome and leave-message are administrative; handled separately inside welcome module
                    "welcome" => { let _ = welcome::handle_welcome_command(&ctx, &command).await; }
                    "leave-message" => { let _ = welcome::handle_leave_command(&ctx, &command).await; }
                    "milestonetest" => { let _ = welcome::handle_milestone_test(&ctx, &command).await; }
                    _ => {}
                }
            }
            serenity::model::interactions::Interaction::MessageComponent(comp) => {
                // handle delete button
                if comp.data.custom_id == "delete_embed_button" {
                    let _ = comp.message.delete(&ctx.http).await;
                    let _ = comp.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::interactions::InteractionResponseType::DeferredUpdateMessage)).await;
                }
            }
            _ => {}
        }
    }

    async fn guild_member_addition(&self, ctx: Context, new_member: serenity::model::guild::Member) {
        // Delegate to welcome module
        let _ = welcome::handle_member_join(&ctx, new_member).await;
    }

    async fn guild_member_removal(&self, ctx: Context, guild_id: serenity::model::id::GuildId, user: serenity::model::user::User, _member: Option<serenity::model::guild::Member>) {
        // Delegate to welcome module
        let _ = welcome::handle_member_remove(&ctx, guild_id, user.id).await;
    }

    async fn message(&self, ctx: Context, msg: serenity::model::channel::Message) {
        // delegate to message link cog
        let _ = messagelink::handle_message(&ctx, &msg).await;
        // delegate to zikosyokai for channel template maintenance
        let _ = zikosyokai::handle_message(&ctx, &msg).await;
    }

    async fn message_delete(&self, ctx: Context, channel_id: serenity::model::id::ChannelId, deleted_message_id: serenity::model::id::MessageId, guild_id: Option<serenity::model::id::GuildId>) {
        let _ = zikosyokai::handle_message_delete(&ctx, deleted_message_id, guild_id).await;
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();

    // Load .env file if present (dotenvy)
    let _ = dotenvy::dotenv();

    let token = match env::var("DISCORD_TOKEN") {
        Ok(t) => t,
        Err(_) => {
            eprintln!("ERROR: DISCORD_TOKEN environment variable not found.\nCreate a .env file with DISCORD_TOKEN=<your token> or set it in the environment.\nSee .env.example for format.");
            return Err(anyhow::anyhow!("DISCORD_TOKEN not set"));
        }
    };

    let intents = GatewayIntents::GUILDS
        | GatewayIntents::GUILD_MEMBERS
        | GatewayIntents::GUILD_MESSAGES
        | GatewayIntents::MESSAGE_CONTENT
        | GatewayIntents::GUILD_MESSAGE_REACTIONS;

    let mut client = serenity::Client::builder(&token, intents)
        .event_handler(Handler)
        .await?;

    // Initialize database
    db::init_db(&client.cache_and_http.http).await.expect("DB init failed");

    // Start client
    client.start().await?;
    Ok(())
}
