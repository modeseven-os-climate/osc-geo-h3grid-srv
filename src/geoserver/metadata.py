# Copyright 2024 Broda Group Software Inc.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.
#
# Created: 2024-03-08 by davis.broda@brodagroupsoftware.com
import logging
import os
from typing import Dict, List, Any

import duckdb
from duckdb.duckdb import ConstraintException

from geoserver.bgsexception import InvalidCharacterException,\
    InvalidColumnTypeException, \
    InvalidArgumentException, BgsAlreadyExistsException
from geoserver.utilities import duckdbutils

METADATA_DB_NAME = "dataset_metadata"
METADATA_TABLE_NAME = "dataset_metadata"

VALID_META_INTERVALS = [
    "one_time",
    "yearly",
    "monthly",
    "daily"
]

VALID_DATASET_TYPES = [
    "h3",
    "point"
]

# Set up logging
LOGGING_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)
logger = logging.getLogger(__name__)


class MetadataDB:

    def __init__(
            self,
            database_dir: str
    ):
        self.database_dir = database_dir
        if not os.path.exists(self.database_dir):
            logger.info(f"metadata database directory {database_dir} did not"
                        f"exist. Creating this directory now.")
            os.makedirs(self.database_dir)

    def add_metadata_entry(
            self,
            dataset_name: str,
            description: str,
            value_columns: Dict[str, str],
            interval: str,
            dataset_type: str
    ) -> str:
        """
        Create a metadata entry for a dataset

        :param dataset_name: The name of the dataset. Must be unique
        :type dataset_name: str
        :param description: A description of the dataset's contents
        :type description: str
        :param value_columns:
            A dictionary of column names mapped to the duckdb database type
            of the data to be contained in that column
        :type value_columns: Dict[str, str]
        :param interval:
            What interval is the data available for/updated for.
            Options: [yearly, monthly, daily]
        :type interval: str
        :param dataset_type:
            The type of dataset to create.
            Options: [h3, point]
        :type dataset_type: str
        :return: The name of the dataset created
        :rtype: str

        :raises BgsAlreadyExistsException:
            if the dataset being created already exists
        """

        if dataset_name == METADATA_DB_NAME:
            raise Exception(f"name {METADATA_DB_NAME} is reserved,"
                            f" and cannot be used as a dataset name.")

        for col_name in value_columns.keys():
            non_al_num = self._get_non_alphanum_chars(col_name)
            if len(non_al_num) > 0:
                raise InvalidCharacterException(
                    f"column names must be alphanumeric."
                    f" column name: [{col_name}] contained"
                    f" non-alphanumeric character(s): [{non_al_num}]"
                )

        col_errors = []
        for k, v in value_columns.copy().items():
            is_valid, error = duckdbutils.is_general_col_type(v)
            if is_valid:
                cannonical_type = duckdbutils.convert_to_cannonical_type(v)
                value_columns[k] = cannonical_type
            else:
                full_error = f"column {k} was found invalid for reason: {error}"
                col_errors.append(full_error)

        if len(col_errors) > 0:
            raise InvalidColumnTypeException(
                "One or more column types was found invalid:"
                f"{col_errors}"
            )

        if dataset_type not in VALID_DATASET_TYPES:
            raise InvalidArgumentException(
                f"dataset type: {dataset_type} was not valid."
                f" Valid dataset types are: {VALID_DATASET_TYPES}"
            )

        if interval not in VALID_META_INTERVALS:
            raise InvalidArgumentException(
                f"interval: {interval} was not valid."
                f" Valid intervals are: {VALID_META_INTERVALS}"
            )

        out_db_path = self._get_db_path(METADATA_DB_NAME)
        connection = duckdb.connect(database=out_db_path)

        # one dataset, with year, month, day
        # and have a time-reslution thing that says whether monthly, daily, etc. data is available
        if not duckdbutils.duckdb_check_table_exists(
                connection, METADATA_TABLE_NAME
        ):
            # name to identify
            # dataset_type to let us know what type of data is in the dataset
            #   available types: h3, point
            # interval is for what time period data is available
            #  (yearly, monthly, daily, etc.)
            create_sql = f"""
                CREATE TABLE IF NOT EXISTS {METADATA_TABLE_NAME} (
                    dataset_name    VARCHAR PRIMARY KEY,
                    description     VARCHAR,
                    value_columns   MAP(VARCHAR, VARCHAR),
                    interval        VARCHAR,
                    dataset_type    VARCHAR
                )
            """
            connection.execute(create_sql)

        insert = f"""
            INSERT INTO {METADATA_TABLE_NAME} VALUES (?,?,?,?,?)
        """

        # This format is necessary for duckdb to recognize this as a MAP
        #  instead of a STRUCT
        val_col_map = {
            "key": list(value_columns.keys()),
            "value": list(value_columns.values())
        }

        try:
            connection.execute(
                insert,
                [dataset_name, description, val_col_map, interval, dataset_type]
            )
        except ConstraintException as e:
            raise BgsAlreadyExistsException(
                f"dataset with name {dataset_name} already exists",
                e
            ) from e
        finally:
            connection.close()

        logger.info(f"added entry for dataset {dataset_name}")
        return f"{dataset_name}"


    def show_meta(self) -> List[Dict[str, Any]]:
        out_db_path = self._get_db_path(METADATA_DB_NAME)
        connection = duckdb.connect(database=out_db_path)

        if not duckdbutils.duckdb_check_table_exists(
                connection, METADATA_TABLE_NAME
        ):
            raise Exception(f"{METADATA_TABLE_NAME} table does not exist")

        sql = f"""
            SELECT 
                dataset_name,
                description,
                value_columns,
                interval,
                dataset_type
            FROM {METADATA_TABLE_NAME}
        """
        result_raw = connection.execute(sql).fetchall()

        out = []

        for row in result_raw:
            result = {
                "dataset_name": row[0],
                "description": row[1],
                "value_columns": row[2],
                "interval": row[3],
                "dataset_type": row[4]
            }
            out.append(result)

        return out


    def ds_meta_exists(self, dataset_name: str) -> bool:
        out_db_path = self._get_db_path(METADATA_DB_NAME)
        connection = duckdb.connect(database=out_db_path)

        if not duckdbutils.duckdb_check_table_exists(
                connection, METADATA_TABLE_NAME
        ):
            raise Exception(
                f"{METADATA_TABLE_NAME} table does not exist"
                f" in database {out_db_path}")

        sql = f"""
                    SELECT count(*)
                    FROM {METADATA_TABLE_NAME}
                    WHERE dataset_name = ?
                """
        result_raw = connection.execute(sql, [dataset_name]).fetchone()

        return result_raw[0] == 1


    def get_ds_metadata(self, dataset_name: str) -> Dict[str, Any]:
        out_db_path = self._get_db_path(METADATA_DB_NAME)
        connection = duckdb.connect(database=out_db_path)

        if not duckdbutils.duckdb_check_table_exists(
                connection, METADATA_TABLE_NAME
        ):
            raise Exception(f"{METADATA_TABLE_NAME} table does not exist")

        sql = f"""
           SELECT 
                dataset_name,
                description,
                value_columns,
                interval,
                dataset_type
           FROM {METADATA_TABLE_NAME}
           WHERE dataset_name = ?
        """
        result_raw = connection.execute(sql, [dataset_name]).fetchone()

        result = {
            "dataset_name": result_raw[0],
            "description": result_raw[1],
            "value_columns": result_raw[2],
            "interval": result_raw[3],
            "dataset_type": result_raw[4]
        }

        col_names: List[str] = result["value_columns"]["key"]

        for c_name in col_names:
            non_al_num = self._get_non_alphanum_chars(c_name)
            if len(non_al_num) > 0:
                raise InvalidCharacterException(
                    f"column names must be alphanumeric."
                    f" column name: [{c_name}] contained"
                    f" non-alphanumeric character(s): [{non_al_num}]"
                )

        return result


    def _get_non_alphanum_chars(self, s: str) -> str:
        char_to_remove = ''.join(filter(lambda x: x.isalnum(), s))
        table = str.maketrans("", "", char_to_remove)
        non_alpha = s.translate(table)
        return non_alpha

    def _get_db_path(self, db_name: str) -> str:
        return os.path.join(self.database_dir, f"{db_name}.duckdb")
