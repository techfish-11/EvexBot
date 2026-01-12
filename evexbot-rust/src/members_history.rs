use anyhow::Result;
use chrono::{NaiveDate, NaiveDateTime, DateTime, Utc, Datelike};
use plotters::prelude::*;
use serenity::model::application::interaction::application_command::ApplicationCommandInteraction;
use serenity::prelude::*;

pub async fn handle_members_history(ctx: &serenity::prelude::Context, command: &ApplicationCommandInteraction) -> Result<()> {
    // Defer response
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;

    let mut start_date = None;
    let mut end_date = None;
    for opt in &command.data.options {
        match opt.name.as_str() {
            "start_date" => { if let Some(v) = opt.value.as_ref() { if let Some(s) = v.as_str() { start_date = Some(parse_date(s)?); } } }
            "end_date" => { if let Some(v) = opt.value.as_ref() { if let Some(s) = v.as_str() { end_date = Some(parse_date(s)?); } } }
            _ => {}
        }
    }

    let start_date = start_date.ok_or_else(|| anyhow::anyhow!("start_date required"))?;
    let end_date = end_date.ok_or_else(|| anyhow::anyhow!("end_date required"))?;
    if start_date > end_date { command.create_followup_message(&ctx.http, |m| m.content("開始日は終了日より前である必要があります。" ) ).await?; return Ok(()); }
    if (end_date - start_date).num_days() > 365 * 3 { command.create_followup_message(&ctx.http, |m| m.content("日付の範囲は最大3年までにしてください。" ) ).await?; return Ok(()); }

    // fetch join dates
    let guild = command.guild_id.ok_or_else(|| anyhow::anyhow!("Guild only command"))?;
    let join_dates = fetch_all_join_dates(&ctx, guild).await?;
    if join_dates.is_empty() { command.create_followup_message(&ctx.http, |m| m.content("参加履歴が見つかりません。メンバーの参加日時が取得できませんでした。" ) ).await?; return Ok(()); }

    let (dates, counts) = generate_counts(&join_dates, start_date, end_date);
    let buf = create_plot(&dates, &counts)?;

    let mut embed = serenity::builder::CreateEmbed::default();
    embed.title("Member Count History");
    embed.description(format!("{} から {} までのメンバー数推移", start_date, end_date));
    embed.color(serenity::utils::Colour::BLURPLE);
    embed.field("開始時点のメンバー数", counts.first().map(|c| c.to_string()).unwrap_or("0".to_string()), true);
    embed.field(&format!("{}時点のメンバー数", end_date), counts.last().map(|c| c.to_string()).unwrap_or("0".to_string()), true);
    embed.image("attachment://members_history.png");

    command.create_followup_message(&ctx.http, |m| m.add_file((buf.as_slice(), "members_history.png")).embed(|e| { *e = embed; e })).await?;

    Ok(())
}

fn parse_date(s: &str) -> Result<NaiveDate> {
    if let Ok(dt) = NaiveDate::parse_from_str(s, "%Y-%m-%d") { return Ok(dt); }
    if let Ok(dt) = NaiveDate::parse_from_str(s, "%Y/%m/%d") { return Ok(dt); }
    Err(anyhow::anyhow!("日付は YYYY-MM-DD または YYYY/MM/DD の形式で指定してください。"))
}

async fn fetch_all_join_dates(ctx: &Context, guild_id: serenity::model::id::GuildId) -> Result<Vec<NaiveDateTime>> {
    let mut dates = Vec::new();
    let members = ctx.http.get_guild_members(guild_id.0, None, None).await?;
    for m in members.into_iter() {
        if let Some(j) = m.joined_at {
            if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(&j.to_string()) {
                dates.push(dt.naive_utc());
            }
        }
    }
    dates.sort();
    Ok(dates)
}

fn generate_counts(join_dates: &Vec<NaiveDateTime>, start: NaiveDate, end: NaiveDate) -> (Vec<NaiveDate>, Vec<i32>) {
    let days = (end - start).num_days() as usize + 1;
    let dates: Vec<NaiveDate> = (0..days).map(|i| start + chrono::Duration::days(i as i64)).collect();
    let jd_nums: Vec<i32> = join_dates.iter().map(|d| d.date().num_days_from_ce()).collect();
    let counts: Vec<i32> = dates.iter().map(|d| {
        let dn = d.num_days_from_ce();
        jd_nums.iter().filter(|&&j| j <= dn).count() as i32
    }).collect();    (dates, counts)
}

fn create_plot(dates: &Vec<NaiveDate>, counts: &Vec<i32>) -> Result<Vec<u8>> {
    use plotters_bitmap::BitMapBackend;
    let width = 1200usize; let height = 400usize;
    let mut buf = vec![0u8; width * height * 3];

    let days = dates.len();
    let max_count = counts.iter().copied().max().unwrap_or(0) + 1;

    {
        let backend = BitMapBackend::with_buffer(&mut buf, (width as u32, height as u32));
        let drawing = backend.into_drawing_area();
        drawing.fill(&WHITE)?;

        let mut chart = ChartBuilder::on(&drawing)
            .margin(10)
            .caption("Member Count History", ("sans-serif", 20))
            .x_label_area_size(35)
            .y_label_area_size(40)
            .build_cartesian_2d(0usize..days, 0i32..max_count)?;

        chart.configure_mesh().disable_mesh().x_labels(6).x_label_formatter(&|v| dates[*v].to_string()).draw()?;

        chart.draw_series(LineSeries::new((0..days).map(|i| (i, counts[i])), &BLUE))?;

        drawing.present()?;
    }

    let image = image::RgbImage::from_raw(width as u32, height as u32, buf).ok_or_else(|| anyhow::anyhow!("Failed to create image"))?;
    let mut out = Vec::new();
    image::DynamicImage::ImageRgb8(image).write_to(&mut std::io::Cursor::new(&mut out), image::ImageOutputFormat::Png)?;
    Ok(out)
}
