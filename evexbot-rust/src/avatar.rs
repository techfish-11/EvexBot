use anyhow::Result;
use serenity::model::application::interaction::application_command::ApplicationCommandInteraction;
use serenity::prelude::*;

pub async fn handle_avatar(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;
    let user = command.data.options.get(0).and_then(|o| o.resolved.as_ref()).and_then(|r| match r { serenity::model::prelude::application_command::CommandDataOptionValue::User(u, _member) => Some(u.clone()), _ => None }).unwrap_or(command.user.clone());

    if let Some(avatar_url) = user.avatar_url() {
        command.create_followup_message(&ctx.http, |m| {
            m.embed(|e| {
                e.title(format!("{}のアイコン", user.name));
                e.image(&avatar_url);
                e
            })
        }).await?;
    } else {
        let default_url = user.default_avatar_url();
        command.create_followup_message(&ctx.http, |m| {
            m.embed(|e| {
                e.title(format!("{}のデフォルトアイコン", user.name));
                e.image(&default_url);
                e
            })
        }).await?;
    }

    Ok(())
}
