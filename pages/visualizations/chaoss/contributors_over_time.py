from dash import html, dcc
import dash
import dash_bootstrap_components as dbc
from dash import callback
from dash.dependencies import Input, Output, State
import pandas as pd
import logging
import numpy as np
import plotly.express as px
from pages.utils.graph_utils import get_graph_time_values, color_seq

from pages.utils.job_utils import nodata_graph
from queries.contributors_query import contributors_query as ctq
import time
import io
from cache_manager.cache_manager import CacheManager as cm

gc_contributors_over_time = dbc.Card(
    [
        dbc.CardBody(
            [
                html.H3(
                    "Contributor Types Over Time",
                    className="card-title",
                    style={"text-align": "center"},
                ),
                dbc.Popover(
                    [
                        dbc.PopoverHeader("Graph Info:"),
                        dbc.PopoverBody("Information on graph 3"),
                    ],
                    id="chaoss-popover-3",
                    target="chaoss-popover-target-3",  # needs to be the same as dbc.Button id
                    placement="top",
                    is_open=False,
                ),
                dcc.Loading(
                    dcc.Graph(id="contributors-over-time"),
                ),
                dbc.Form(
                    [
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Contributions Required:",
                                    html_for="num_contribs_req",
                                    width={"size": "auto"},
                                ),
                                dbc.Col(
                                    dbc.Input(
                                        id="num_contribs_req",
                                        type="number",
                                        min=1,
                                        max=15,
                                        step=1,
                                        value=4,
                                        size="sm",
                                    ),
                                    className="me-2",
                                    width=1,
                                ),
                            ],
                            align="center",
                        ),
                        dbc.Row(
                            [
                                dbc.Label(
                                    "Date Interval:",
                                    html_for="contrib-time-interval",
                                    width="auto",
                                ),
                                dbc.Col(
                                    dbc.RadioItems(
                                        id="contrib-time-interval",
                                        options=[
                                            {
                                                "label": "Week",
                                                "value": "W",
                                            },
                                            {"label": "Month", "value": "M"},
                                            {"label": "Year", "value": "Y"},
                                        ],
                                        value="M",
                                        inline=True,
                                    ),
                                    className="me-2",
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "About Graph",
                                        id="chaoss-popover-target-3",
                                        color="secondary",
                                        size="sm",
                                    ),
                                    width="auto",
                                    style={"padding-top": ".5em"},
                                ),
                            ],
                            align="center",
                        ),
                    ]
                ),
            ]
        ),
    ],
    # color="light",
)


@callback(
    Output("chaoss-popover-3", "is_open"),
    [Input("chaoss-popover-target-3", "n_clicks")],
    [State("chaoss-popover-3", "is_open")],
)
def toggle_popover_3(n, is_open):
    if n:
        return not is_open
    return is_open


@callback(
    Output("contributors-over-time", "figure"),
    [
        Input("repo-choices", "data"),
        Input("num_contribs_req", "value"),
        Input("contrib-time-interval", "value"),
    ],
    background=True,
)
def create_contrib_over_time_graph(repolist, contribs, interval):

    # wait for data to asynchronously download and become available.
    cache = cm()
    df = cache.grabm(func=ctq, repos=repolist)
    while df is None:
        time.sleep(1.0)
        df = cache.grabm(func=ctq, repos=repolist)

    start = time.perf_counter()
    logging.debug("CONTRIB_DRIVE_REPEAT_VIZ - START")

    # test if there is data
    if df.empty:
        logging.debug("PULL REQUESTS OVER TIME - NO DATA AVAILABLE")
        return nodata_graph

    # function for all data pre processing
    df_drive_repeat = process_data(df, interval, contribs)

    fig = create_figure(df_drive_repeat, interval)

    logging.debug(f"CONTRIBUTIONS_OVER_TIME_VIZ - END - {time.perf_counter() - start}")
    return fig


def process_data(df, interval, contribs):
    # convert to datetime objects with consistent column name
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df.rename(columns={"created_at": "created"}, inplace=True)

    # remove null contrib ids
    df.dropna(inplace=True)

    # create column for identifying Drive by and Repeat Contributors
    contributors = df["cntrb_id"][df["rank"] == contribs].to_list()

    # dfs for drive by and repeat contributors
    df_drive_temp = df.loc[~df["cntrb_id"].isin(contributors)]
    df_repeat_temp = df.loc[df["cntrb_id"].isin(contributors)]

    # order values chronologically by creation date
    df = df.sort_values(by="created", axis=0, ascending=True)

    # variable to slice on to handle weekly period edge case
    period_slice = None
    if interval == "W":
        # this is to slice the extra period information that comes with the weekly case
        period_slice = 10

    # df for drive by contributros in time interval
    df_drive = (
        # disable and re-enable formatter
        # fmt: off
        df_drive_temp.groupby(by=df_drive_temp.created.dt.to_period(interval))["cntrb_id"]
        # fmt: on
        .nunique()
        .reset_index()
        .rename(columns={"cntrb_id": "Drive", "created": "Date"})
    )
    df_drive["Date"] = pd.to_datetime(df_drive["Date"].astype(str).str[:period_slice])

    # df for repeat contributors in time interval
    df_repeat = (
        # disable and re-enable formatter
        # fmt: off
        df_repeat_temp.groupby(by=df_repeat_temp.created.dt.to_period(interval))["cntrb_id"]
        # fmt: on
        .nunique()
        .reset_index()
        .rename(columns={"cntrb_id": "Repeat", "created": "Date"})
    )
    df_repeat["Date"] = pd.to_datetime(df_repeat["Date"].astype(str).str[:period_slice])

    # A single df created for plotting merged and closed as stacked bar chart
    df_drive_repeat = pd.merge(df_drive, df_repeat, on="Date", how="outer")

    # formating for graph generation
    if interval == "M":
        df_drive_repeat["Date"] = df_drive_repeat["Date"].dt.strftime("%Y-%m-01")
    elif interval == "Y":
        df_drive_repeat["Date"] = df_drive_repeat["Date"].dt.strftime("%Y-01-01")

    return df_drive_repeat


def create_figure(df_drive_repeat, interval):
    # time values for graph
    x_r, x_name, hover, period = get_graph_time_values(interval)

    fig = px.bar(
        df_drive_repeat,
        x="Date",
        y=["Repeat", "Drive"],
        labels={"x": x_name, "y": "Contributors"},
        color_discrete_sequence=[color_seq[1], color_seq[4]],
    )
    fig.update_traces(
        hovertemplate=hover + "<br>Contributors: %{y}<br><extra></extra>",
    )
    fig.update_xaxes(
        showgrid=True,
        ticklabelmode="period",
        dtick=period,
        rangeslider_yaxis_rangemode="match",
        range=x_r,
    )
    fig.update_layout(
        xaxis_title=x_name,
        legend_title_text="Type",
        yaxis_title="Number of Contributors",
        margin_b=40,
        font=dict(size=14),
    )

    return fig
