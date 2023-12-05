import sqlite3
from sqlite3 import Error


class SqlDb:
    def __init__(self, db_path):
        self.db_path = db_path
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        print('DB: Connected: ', self.db)
        self.create_tables_cols('settings', ('key', 'value'))
        self.create_tables_cols('clients', ('id', 'hostname',))
        self.create_tables_cols('peers', ('id', 'hostname', 'version'))
        self.create_tables_cols('srv', ('id', 'hostname', 'passphrase', 'ping'))
        self.create_tables_cols('cicd_settings', ('key', 'value'))

    def create_cursor(self):
        cursor = self.db.cursor()
        return cursor

    def create_tables_cols(self, table: str, cols: tuple):
        cursor = self.create_cursor()
        if not cols[:1] == '(':
            name_column = f'({cols})'
        cursor.execute(f'CREATE TABLE IF NOT EXISTS "{table}" {cols} ')
        # print(f'DB:')
        self.db.commit()

    def get(self, name_table, name_column, value, res_cols='*'):
        cursor = self.create_cursor()
        if type(value) is int:
            cursor.execute(f"SELECT {res_cols} FROM {name_table} WHERE {name_column}={value}")
        else:
            cursor.execute(f"SELECT {res_cols} FROM {name_table} WHERE {name_column}='{value}'")
        result = cursor.fetchone()
        if result is None:
            # print(f'DB: Table: {name_table} |  Column: {name_column} |  Value: {value}')
            # print(f"SELECT {res_cols} FROM {name_table} WHERE {name_column}={value}")
            # print(f'DB: Data {value} not found in {name_table}')
            return result
        else:
            # print(len(list(result)))
            # return result
            if len(result) == 1:
                return result[0]
            result_from_bd = list(result)
            cursor.execute(f"PRAGMA table_info({name_table})")
            columns = cursor.fetchall()

            column_list = []
            # Цикл сортировки столбцов БД
            for i in columns:
                counter = 0
                for a in i:
                    counter += 1
                    if counter == 2:
                        column_list.append(a)

            dict_from_db = dict(zip(column_list, result_from_bd))
            print('Result:', dict_from_db)
            return dict_from_db

    def set(self, name_table, name_column: tuple, value: tuple):
        cursor = self.create_cursor()
        c0 = name_column[0]
        c1 = name_column[1]
        v0 = value[0]
        v1 = value[1]
        if not str(name_column)[:1] == '(':
            name_column = f'({name_column})'
        if not str(value)[:1] == '(':
            value = f'({value})'
        # print(f'UPDATE {name_table} SET {name_column} VALUES {value}')
        cursor.execute(f'INSERT OR IGNORE INTO {name_table} {name_column} VALUES {value}')
        cursor.execute(f"UPDATE {name_table} SET {c1}='{v1}' WHERE {c0}='{v0}'")
        self.db.commit()
        print(f'DB: Data "{value}" added successfully')

    def update(self, name_table, name_column=None, value=None, column=None, row=None):
        cursor = self.create_cursor()
        if not str(name_column)[:1] == '(':
            name_column = f'({name_column})'
        if not str(value)[:1] == '(':
            value = f'({value})'
        cursor.execute(f"UPDATE {name_table} SET {name_column} = {value} WHERE {column} = '{row}'")
        self.db.commit()
        print('DB: Cell updated')

    def del_row(self, name_table, column=None, value=None):
        cursor = self.create_cursor()
        cursor.execute(f'DELETE FROM {name_table} WHERE {column} = {value}')
        self.db.commit()
        print('DB: Row deleted')

    def raw_req(self, sql_query):
        cursor = self.create_cursor()
        cursor.execute(sql_query)
        print(f'DB: executed {sql_query}')
        return cursor.fetchall()

    def check4exist(self, table, column: str, value=None):
        cursor = self.create_cursor()
        # if not str(column)[:1] == '(':
        #     column = f'({column})'
        # if not str(value)[:1] == '(':
        #     value = f'({value})'
        if type(value) is int:
            cursor.execute(f"SELECT * FROM {table} WHERE {column}={value}")
        else:
            # print(f"SELECT * FROM {table} WHERE {column}='{value}'")
            try:
                cursor.execute(f"SELECT * FROM {table} WHERE {column}='{value}'")
            except Exception:
                return False

        # result = len(cursor.fetchall())
        # print(result)
        if len(cursor.fetchall()) > 0:
            return True
        else:
            return False


# if __name__ == '__main__':
#     db = SqlDb('distribution_payloads.db')
#     d = db.raw_req('SELECT * FROM srv WHERE id=123')
#     print(d)
