use anyhow::Result;
use chrono::{NaiveDateTime, DateTime, Utc};
use plotters::prelude::*;
use smartcore::linalg::naive::dense_matrix::DenseMatrix;
use smartcore::linear::linear_regression::LinearRegression;
use std::process::Stdio;
use chrono::Datelike;
use serde::{Serialize, Deserialize};

#[derive(Serialize)]
struct ProphetInput {
    dates: Vec<String>,
    target: usize,
}

#[derive(Deserialize)]
struct ProphetOutput {
    predicted_date: Option<String>,
    image_base64: Option<String>,
}

/// Predict using Prophet helper (Python). Returns (datetime, PNG bytes) if prediction found.
async fn call_prophet_helper(dates: &[NaiveDateTime], target: usize) -> Result<Option<(DateTime<Utc>, Vec<u8>)>> {
    // Prepare python invocation
    let script = std::path::Path::new("scripts/prophet_predict.py");
    if !script.exists() {
        return Ok(None);
    }

    let input = ProphetInput {
        dates: dates.iter().map(|d| d.date().to_string()).collect(),
        target,
    };

    // Try common python executables (works across platforms/venv)
    let candidates: &[(&str, &[&str])] = &[("python", &[]), ("python3", &[]), ("py", &["-3"])];
    let mut child_opt: Option<tokio::process::Child> = None;
    for (exe, extra_args) in candidates.iter() {
        let mut cmd = tokio::process::Command::new(exe);
        for &a in extra_args.iter() { cmd.arg(a); }
        cmd.arg(script).stdin(Stdio::piped()).stdout(Stdio::piped());
        match cmd.spawn() {
            Ok(c) => { child_opt = Some(c); break; }
            Err(_) => continue,
        }
    }

    let mut child = match child_opt {
        Some(c) => c,
        None => return Ok(None), // no python available
    };

    let stdin = child.stdin.take().ok_or_else(|| anyhow::anyhow!("Failed to open stdin"))?;
    let input_text = serde_json::to_vec(&input)?;
    tokio::io::AsyncWriteExt::write_all(&mut tokio::io::BufWriter::new(stdin), &input_text).await?;

    let output = child.wait_with_output().await?;
    if !output.status.success() {
        return Ok(None);
    }

    let out: ProphetOutput = serde_json::from_slice(&output.stdout)?;
    if let Some(date_str) = out.predicted_date {
        let dt = DateTime::parse_from_rfc3339(&date_str).map(|d| d.with_timezone(&Utc))?;
        if let Some(b64) = out.image_base64 {
            let bytes = base64::decode(&b64)?;
            return Ok(Some((dt, bytes)));
        }
        return Ok(Some((dt, vec![])));
    }
    Ok(None)
}

pub async fn predict_and_generate(dates: &[NaiveDateTime], target: usize) -> Result<Option<(DateTime<Utc>, Vec<u8>)>> {
    // Try Prophet helper first
    if let Ok(Some(res)) = call_prophet_helper(dates, target).await {
        return Ok(Some(res));
    }

    // Polynomial regression fallback
    if dates.len() < 2 {
        return Ok(None);
    }

    // Prepare X and y
    let x: Vec<f64> = dates.iter().map(|d| d.date().num_days_from_ce() as f64).collect();
    let y: Vec<f64> = (1..=dates.len()).map(|v| v as f64).collect();

    let degree = 3usize;
    let n = x.len();
    let mut x_poly = Vec::with_capacity(n * (degree + 1));
    for xi in x.iter() {
        for p in 0..=degree {
            x_poly.push(xi.powi(p as i32));
        }
    }

    let x_mat = DenseMatrix::from_array(n, degree + 1, &x_poly);
    let lr = LinearRegression::fit(&x_mat, &y, Default::default())?;

    // predict forward until target or up to N days
    let last_day = *x.last().unwrap() as i64;
    let max_days = 304;
    for d in 0..max_days {
        let day = (last_day + d) as f64;
        let mut feats = Vec::with_capacity(degree + 1);
        for p in 0..=degree { feats.push(day.powi(p as i32)); }
        let pred = lr.predict(&DenseMatrix::from_array(1, degree + 1, &feats))?[0];
        if pred >= target as f64 {
            let dt = chrono::NaiveDate::from_num_days_from_ce(day as i32).and_hms(0,0,0);
            let dt_utc = DateTime::<Utc>::from_utc(dt, Utc);
            // generate plot
            let img = generate_plot(dates, dt_utc, &lr).await?;
            return Ok(Some((dt_utc, img)));
        }
    }

    Ok(None)
}

async fn generate_plot(dates: &[NaiveDateTime], target_date: DateTime<Utc>, lr: &LinearRegression<f64, DenseMatrix<f64>>) -> Result<Vec<u8>> {
    // Draw using plotters
    use plotters_bitmap::BitMapBackend;
    let w = 800;
    let h = 450;
    let mut buf = vec![0u8; w * h * 3];
    {
        let backend = BitMapBackend::with_buffer(&mut buf, (w as u32, h as u32));
        let drawing = backend.into_drawing_area();
        drawing.fill(&WHITE)?;

        // compute points
        let min_day = dates.first().unwrap().date();
        let max_day = target_date.date_naive();
        let days = (max_day - min_day).num_days() as usize + 1;
        let x_vals: Vec<i64> = (0..days).map(|i| (min_day + chrono::Duration::days(i as i64)).num_days_from_ce() as i64).collect();
        let y_actual: Vec<i32> = {
            let mut counts = vec![0i32; days];
            for d in dates.iter() {
                let idx = (d.date() - min_day).num_days() as usize;
                for i in idx..days { counts[i] += 1; }
            }
            counts
        };

        let max_y = y_actual.iter().copied().max().unwrap_or(0) + 5;

        let mut chart = ChartBuilder::on(&drawing)
            .margin(10)
            .caption("Growth Prediction", ("sans-serif", 24))
            .x_label_area_size(35)
            .y_label_area_size(40)
            .build_cartesian_2d(0usize..days, 0i32..(max_y as i32 + 10))?;

        chart.configure_mesh().disable_mesh().x_labels(6).draw()?;

        chart.draw_series(LineSeries::new((0..days).map(|i| (i, y_actual[i])), &BLUE))?;

        // predicted line
        let degree = 3usize;
        let mut preds = Vec::with_capacity(days);
        for i in 0..days {
            let day = x_vals[i] as f64;
            let mut feats = Vec::with_capacity(degree + 1);
            for p in 0..=degree { feats.push(day.powi(p as i32)); }
            let predv = lr.predict(&DenseMatrix::from_array(1, degree + 1, &feats))?[0];
            preds.push(predv as i32);
        }
        chart.draw_series(LineSeries::new((0..days).map(|i| (i, preds[i])), &RED))?;

        drop(chart);
        drawing.present()?;
    }

    // Convert to PNG
    let image = image::RgbImage::from_raw(w as u32, h as u32, buf).ok_or_else(|| anyhow::anyhow!("Failed to create image"))?;
    let mut out = Vec::new();
    image::DynamicImage::ImageRgb8(image).write_to(&mut std::io::Cursor::new(&mut out), image::ImageOutputFormat::Png)?;
    Ok(out)
}

use serenity::model::application::interaction::application_command::ApplicationCommandInteraction;
use serenity::prelude::*;

pub async fn handle_growth(ctx: &Context, command: &ApplicationCommandInteraction) -> Result<()> {
    command.create_interaction_response(&ctx.http, |r| r.kind(serenity::model::application::interaction::InteractionResponseType::DeferredChannelMessageWithSource)).await?;

    let mut model = "polynomial".to_string();
    let mut target = 0usize;
    let mut show_graph = true;

    for opt in &command.data.options {
        match opt.name.as_str() {
            "model" => { if let Some(v) = opt.value.as_ref() { if let Some(s) = v.as_str() { model = s.to_string(); } } }
            "target" => { if let Some(v) = opt.value.as_ref() { if let Some(n) = v.as_i64() { target = n as usize; } } }
            "show_graph" => { if let Some(v) = opt.value.as_ref() { if let Some(b) = v.as_bool() { show_graph = b; } } }
            _ => {}
        }
    }

    if target == 0 { command.create_followup_message(&ctx.http, |m| m.content("targetを指定してください。" )).await?; return Ok(()); }
    let guild = command.guild_id.ok_or_else(|| anyhow::anyhow!("Guild only"))?;
    let join_dates = {
        let mut dts = Vec::new();
        let members = ctx.http.get_guild_members(guild.0, None, None).await?;
        for m in members.into_iter() {
            if let Some(j) = m.joined_at {
                if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(&j.to_string()) {
                    dts.push(dt.naive_utc());
                }
            }
        }
        dts.sort(); dts
    };
    if join_dates.len() < 2 { command.create_followup_message(&ctx.http, |m| m.content("回帰分析を行うためのデータが不足しています。" )).await?; return Ok(()); }

    if model == "prophet" {
        // try prophet helper
        if let Ok(Some((dt, img))) = crate::growth::call_prophet_helper(&join_dates, target).await {
            let mut embed = serenity::builder::CreateEmbed::default();
            embed.title("Server Growth Prediction");
            embed.description(format!("{}人に達する予測日: {}", target, dt.date_naive()));
            embed.color(serenity::utils::Colour::BLUE);
            if show_graph && !img.is_empty() {
                embed.image("attachment://growth_prediction.png");
                command.create_followup_message(&ctx.http, |m| m.add_file((img.as_slice(), "growth_prediction.png")).embed(|e| { *e = embed; e })).await?;
                return Ok(());
            }
            command.create_followup_message(&ctx.http, |m| m.embed(|e| { *e = embed; e })).await?;
            return Ok(());
        } else {
            command.create_followup_message(&ctx.http, |m| m.content("予測できませんでした。" )).await?;
            return Ok(());
        }
    } else {
        // polynomial fallback handled here
        if let Ok(Some((dt, img))) = predict_and_generate(&join_dates, target).await {
            let mut embed = serenity::builder::CreateEmbed::default();
            embed.title("Server Growth Prediction");
            embed.description(format!("{}人に達する予測日: {}", target, dt.date_naive()));
            embed.color(serenity::utils::Colour::BLUE);
            if show_graph && !img.is_empty() {
                embed.image("attachment://growth_prediction.png");
                command.create_followup_message(&ctx.http, |m| m.add_file((img.as_slice(), "growth_prediction.png")).embed(|e| { *e = embed; e })).await?;
            } else {
                command.create_followup_message(&ctx.http, |m| m.embed(|e| { *e = embed; e })).await?;
            }
            return Ok(());
        } else {
            command.create_followup_message(&ctx.http, |m| m.content("予測できませんでした。" )).await?;
            return Ok(());
        }
    }
}

