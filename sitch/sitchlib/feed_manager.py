import csv
import gzip
import os
import re
import requests
import sqlite3
import time
from datetime import datetime
from utility import Utility


class FeedManager(object):
    def __init__(self, config):
        self.mcc_list = config.mcc_list
        self.state_list = config.state_list
        self.feed_dir = config.feed_dir
        self.url_base = config.feed_url_base
        # self.feed_cache = []
        self.cgi_feed_files = []
        self.arfcn_feed_files = []
        self.born_on_date = datetime.now()
        self.cgi_db = os.path.join(self.feed_dir, "cgi.db")
        self.newest_record_file = os.path.join(self.feed_dir, "newest_record")
        self.arfcn_db = os.path.join(self.feed_dir, "arfcn.db")

    def update_feed_files(self):
        # all_feed_ids = self.state_list + self.mcc_list
        for feed_id in self.mcc_list:
            feed_file = FeedManager.place_feed_file(self.feed_dir,
                                                    self.url_base,
                                                    feed_id)
            self.cgi_feed_files.append(feed_file)
        for feed_id in self.state_list:
            feed_file = FeedManager.place_feed_file(self.feed_dir,
                                                    self.url_base,
                                                    feed_id)
            self.arfcn_feed_files.append(feed_file)
        print("Feed: Finished pulling all feed files")
        return

    def update_feed_db(self):
        last_timestamp = self.get_newest_record_time()
        this_timestamp = FeedManager.reconcile_cgi_db(self.cgi_feed_files,
                                                      self.cgi_db,
                                                      last_timestamp)
        self.set_newest_record_time(this_timestamp)

    def get_newest_record_time(self):
        result = 0
        rx = r'^\d{10}$'
        if not os.path.isfile(self.newest_record_file):
            print("FeedManager: No record of last update found...")
            return result
        with open(self.newest_record_file, 'r') as u_file:
            first_line = u_file.readline().replace('\n', '')
            if re.match(rx, first_line):
                result = first_line
                print("FeedManager: Newest DB record timestamp is %s" % Utility.epoch_to_iso8601(result))  # NOQA
            else:
                print("FeedManager: Unable to parse newest DB record timestamp!")  # NOQA
        return result

    def set_newest_record_time(self, timestamp):
        with open(self.newest_record_file, 'w') as u_file:
            print("FeedManager: Setting newest DB record to %s" % Utility.epoch_to_iso8601(timestamp))  # NOQA
            u_file.write(str(timestamp))
        return

    @classmethod
    def reconcile_cgi_db(cls, feed_files, db_file, last_update):
        db_exists = os.path.isfile(db_file)
        schema = ["radio", "mcc", "net", "area", "cell",
                  "unit", "lon", "lat", "range", "carrier"]
        # If DB file does not exist, create it, then rip the DB from file
        if not db_exists:
            ts = cls.create_and_populate_cgi_db(schema, feed_files, db_file)
        else:
            ts = cls.merge_feed_files_into_db(schema, feed_files,
                                              db_file, last_update)
        return ts

    @classmethod
    def merge_feed_files_into_db(cls, schema, feed_files, db_file, last_upd):
        newest_ts_overall = float(0)
        for feed_file in feed_files:
            feed_file_exists = os.path.isfile(feed_file)
            if not feed_file_exists:
                print("FeedManager: Feed file does not exist: %s" % feed_file)
            else:
                newest_ts = cls.cgi_csv_dump_to_db(schema, feed_file, db_file, last_upd)  # NOQA
                if newest_ts > newest_ts_overall:
                    newest_ts_overall = float(newest_ts)
        return newest_ts_overall

    @classmethod
    def create_and_populate_cgi_db(cls, schema, feed_files, db_file):
        newest_ts_overall = float(0)  # Newest timestamp
        cls.create_cgi_db(db_file, schema)
        for feed_file in feed_files:
            feed_file_exists = os.path.isfile(feed_file)
            if not feed_file_exists:
                print("FeedManager: Feed file does not exist: %s" % feed_file)
            else:
                newest_ts = cls.cgi_csv_dump_to_db(schema, feed_file, db_file)
                if newest_ts > newest_ts_overall:
                    newest_ts_overall = float(newest_ts)
        return newest_ts_overall

    @classmethod
    def should_update_record(cls, anchor_time, update_time):
        """This function returns True if create_time or update_time are newer
        than anchor_time
        """
        if update_time > anchor_time:
            result = True
        else:
            result = False
        return result

    @classmethod
    def cgi_csv_dump_to_db(cls, schema, feed_file, db_file, last_upd=0):
        """Retuens the latest timestamp detected in the feed file"""
        proc_chunk = []
        rows_written = 0
        latest_timestamp = float(0)
        with gzip.open(feed_file, 'r') as feed_file:
            feed_file = csv.DictReader(feed_file)
            for row in feed_file:
                if not cls.should_update_record(last_upd, row["updated"]):
                    continue
                if latest_timestamp < float(row["updated"]):
                    latest_timestamp = float(row["updated"])
                if len(proc_chunk) < 9999:
                    proc_chunk.append(cls.tup_from_row(schema, row))
                else:
                    proc_chunk.append(cls.tup_from_row(schema, row))
                    cls.cgi_mass_insert(schema, proc_chunk, db_file)
                    rows_written += len(proc_chunk)
                    msg = "FeedManager: %s rows written to %s" % (str(rows_written), db_file)  # NOQA
                    print msg
                    proc_chunk = []
        cls.cgi_mass_insert(schema, proc_chunk, db_file)
        rows_written += len(proc_chunk)
        msg = "FeedManager: %s rows written to %s. Done." % (str(rows_written), db_file)  # NOQA
        print msg
        return latest_timestamp

    @classmethod
    def cgi_mass_insert(cls, schema, rows, db_file):
        conn = sqlite3.connect(db_file)
        conn.executemany("INSERT INTO cgi VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

    @classmethod
    def tup_from_row(cls, schema, row):
        retlst = []
        for s in schema:
            retlst.append(row[s])
        return tuple(retlst)

    @classmethod
    def create_cgi_db(cls, cgi_db, schema):
        conn = sqlite3.connect(cgi_db)
        print("FeedManager: Creating CGI DB at %s" % cgi_db)
        create_table_str = ("create table cgi (radio varchar, mcc varchar, " +
                            "net varchar, area varchar, cell varchar, " +
                            "unit varchar, lon varchar, lat varchar, " +
                            "range varchar, carrier varchar, " +
                            "UNIQUE (mcc, net, area, cell) " +
                            "ON CONFLICT REPLACE);")
        conn.execute(create_table_str)
        conn.close()

    @classmethod
    def place_feed_file(cls, feed_dir, url_base, item_id):
        """ Retrieves and places feed files for use by the Enricher modules

        Args:
            feed_dir (str): Destination directory for feed files
            url_base (str): Base URL for hosted feed files
            item_id(str): For FCC, this is the two-letter ("CA" or "TN",
             for example), which is used in the retrieval of the feed file as
             well as the construction of the local feed file name.  For MCC
             this is the MCC, but in string form.  Not integer.

        """
        destination_file = Utility.construct_feed_file_name(feed_dir,
                                                            item_id)
        temp_file = "%s.TEMP" % destination_file
        origin_url = FeedManager.get_source_url(url_base, item_id)
        msg = "Feed: Downloading %s to %s" % (origin_url, temp_file)
        print(msg)
        response = requests.get(origin_url, stream=True)
        with open(temp_file, 'wb') as out_file:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    out_file.write(chunk)
        time.sleep(1)
        print("Feed: Moving %s to %s" % (temp_file, destination_file))
        os.rename(temp_file, destination_file)
        return destination_file

    @classmethod
    def get_source_url(cls, url_base, mcc):
        src_url = "%s/%s.csv.gz" % (url_base, mcc)
        return src_url
