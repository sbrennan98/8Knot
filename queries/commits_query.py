import logging
from db_manager.AugurInterface import AugurInterface
from app_global import celery_app
import pandas as pd
from cache_manager.cache_manager import CacheManager as cm


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    exponential_backoff=2,
    retry_kwargs={"max_retries": 5},
    retry_jitter=True,
)
def commits_query(self, dbmc, repos):
    """
    (Worker Query)
    Executes SQL query against Augur database for commit data.

    Args:
    -----
        dbmc (AugurInterface): Handles connection to Augur database, executes queries and returns results.

        repo_ids ([str]): repos that SQL query is executed on.

    Returns:
    --------
        dict: Results from SQL query, interpreted from pd.to_dict('records')
    """
    logging.debug("COMMITS_DATA_QUERY - START")

    if len(repos) == 0:
        return None

    # commenting-outunused query components. only need the repo_id and the
    # authorship date for our current queries. remove the '--' to re-add
    # the now-removed values.
    query_string = f"""
                    SELECT
                        distinct
                        r.repo_id AS id,
                        -- r.repo_name,
                        c.cmt_commit_hash AS commits,
                        -- c.cmt_id AS file,
                        -- c.cmt_added AS lines_added,
                        -- c.cmt_removed AS lines_removed,
                        c.cmt_author_date AS date
                    FROM
                        repo r
                    JOIN commits c
                        ON r.repo_id = c.repo_id
                    WHERE
                        c.repo_id in ({str(repos)[1:-1]})
                    """

    # create database connection, load config, execute query above.
    dbm = AugurInterface()
    dbm.load_pconfig(dbmc)
    df_commits = dbm.run_query(query_string)

    # break apart returned data per repo
    # and temporarily store in List to be
    # stored in Redis.
    pic = []
    for r in repos:
        # convert series to a dataframe
        # once we've stored the data by ID we no longer need the column.
        c_df = pd.DataFrame(df_commits.loc[df_commits["id"] == r].drop(columns=["id"]))

        # try to convert dates in date-columns to posix timestamps
        try:
            c_df["date"] = pd.Timestamp(c_df["date"]).timestamp()
        except:
            pass

        # add pickled dataframe to list of pickled objects
        pic.append(c_df.to_csv(index=False))

    del df_commits

    # store results in Redis
    cm_o = cm()

    # 'ack' is a boolean of whether data was set correctly or not.
    ack = cm_o.setm(func=commits_query, repos=repos, datas=pic)

    logging.debug("COMMITS_DATA_QUERY - END")
    return ack
