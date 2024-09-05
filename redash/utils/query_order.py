# Copyright (c) 2012, Konsta Vesterinen
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * The names of the contributors may not be used to endorse or promote products
#   derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from inspect import isclass

import sqlalchemy as sa
from sqlalchemy.orm import mapperlib
from sqlalchemy.orm.properties import ColumnProperty
from sqlalchemy.orm.query import _ColumnEntity
from sqlalchemy.orm.util import AliasedInsp
from sqlalchemy.sql.expression import asc, desc


def get_query_descriptor(query, entity, attr):
    if attr in query_labels(query):
        return attr
    entity = get_query_entity_by_alias(query, entity)
    if entity:
        descriptor = get_descriptor(entity, attr)
        if hasattr(descriptor, "property") and isinstance(descriptor.property, sa.orm.RelationshipProperty):
            return
        return descriptor


def query_labels(query):
    """
    Return all labels for given SQLAlchemy query object.
    Example::
        query = session.query(
            Category,
            db.func.count(Article.id).label('articles')
        )
        query_labels(query)  # ['articles']
    :param query: SQLAlchemy Query object
    """
    return [
        entity._label_name for entity in query._entities if isinstance(entity, _ColumnEntity) and entity._label_name
    ]


def get_query_entity_by_alias(query, alias):
    entities = get_query_entities(query)
    if not alias:
        return entities[0]
    for entity in entities:
        if isinstance(entity, sa.orm.util.AliasedClass):
            name = sa.inspect(entity).name
        else:
            name = get_mapper(entity).tables[0].name
        if name == alias:
            return entity


def get_query_entities(query):
    """
    Return a list of all entities present in given SQLAlchemy query object.
    Examples::
        from sqlalchemy_utils import get_query_entities
        query = session.query(Category)
        get_query_entities(query)  # [<Category>]
        query = session.query(Category.id)
        get_query_entities(query)  # [<Category>]
    This function also supports queries with joins.
    ::
        query = session.query(Category).join(Article)
        get_query_entities(query)  # [<Category>, <Article>]
    .. versionchanged: 0.26.7
        This function now returns a list instead of generator
    :param query: SQLAlchemy Query object
    """
    exprs = [
        d["expr"] if is_labeled_query(d["expr"]) or isinstance(d["expr"], sa.Column) else d["entity"]
        for d in query.column_descriptions
    ]
    return [get_query_entity(expr) for expr in exprs] + [get_query_entity(entity) for entity in query._join_entities]


def is_labeled_query(expr):
    return isinstance(expr, sa.sql.elements.Label) and isinstance(
        list(expr.base_columns)[0], (sa.sql.selectable.Select, sa.sql.selectable.ScalarSelect)
    )


def get_query_entity(expr):
    if isinstance(expr, sa.orm.attributes.InstrumentedAttribute):
        return expr.parent.class_
    if isinstance(expr, sa.Column):
        return expr.table
    if isinstance(expr, AliasedInsp):
        return expr.entity
    return expr


def get_mapper(mixed):
    """
    Return related SQLAlchemy Mapper for given SQLAlchemy object.
    :param mixed: SQLAlchemy Table / Alias / Mapper / declarative model object
    ::
        from sqlalchemy_utils import get_mapper
        get_mapper(User)
        get_mapper(User())
        get_mapper(User.__table__)
        get_mapper(User.__mapper__)
        get_mapper(sa.orm.aliased(User))
        get_mapper(sa.orm.aliased(User.__table__))
    Raises:
        ValueError: if multiple mappers were found for given argument
    .. versionadded: 0.26.1
    """
    if isinstance(mixed, sa.orm.query._MapperEntity):
        mixed = mixed.expr
    elif isinstance(mixed, sa.Column):
        mixed = mixed.table
    elif isinstance(mixed, sa.orm.query._ColumnEntity):
        mixed = mixed.expr
    if isinstance(mixed, sa.orm.Mapper):
        return mixed
    if isinstance(mixed, sa.orm.util.AliasedClass):
        return sa.inspect(mixed).mapper
    if isinstance(mixed, sa.sql.selectable.Alias):
        mixed = mixed.element
    if isinstance(mixed, AliasedInsp):
        return mixed.mapper
    if isinstance(mixed, sa.orm.attributes.InstrumentedAttribute):
        mixed = mixed.class_
    if isinstance(mixed, sa.Table):
        mappers = [mapper for mapper in mapperlib._mapper_registry if mixed in mapper.tables]
        if len(mappers) > 1:
            raise ValueError("Multiple mappers found for table '%s'." % mixed.name)
        elif not mappers:
            raise ValueError("Could not get mapper for table '%s'." % mixed.name)
        else:
            return mappers[0]
    if not isclass(mixed):
        mixed = type(mixed)
    return sa.inspect(mixed)


def get_polymorphic_mappers(mixed):
    if isinstance(mixed, AliasedInsp):
        return mixed.with_polymorphic_mappers
    return mixed.polymorphic_map.values()


def get_descriptor(entity, attr):
    mapper = sa.inspect(entity)
    for key, descriptor in get_all_descriptors(mapper).items():
        if attr == key:
            prop = descriptor.property if hasattr(descriptor, "property") else None
            if isinstance(prop, ColumnProperty):
                if isinstance(entity, sa.orm.util.AliasedClass):
                    for c in mapper.selectable.c:
                        if c.key == attr:
                            return c
                else:
                    # If the property belongs to a class that uses
                    # polymorphic inheritance we have to take into account
                    # situations where the attribute exists in child class
                    # but not in parent class.
                    return getattr(prop.parent.class_, attr)
            else:
                # Handle synonyms, relationship properties and hybrid
                # properties
                if isinstance(entity, sa.orm.util.AliasedClass):
                    return getattr(entity, attr)
                try:
                    return getattr(mapper.class_, attr)
                except AttributeError:
                    pass


def get_all_descriptors(expr):
    if isinstance(expr, sa.sql.selectable.Selectable):
        return expr.c
    insp = sa.inspect(expr)
    try:
        polymorphic_mappers = get_polymorphic_mappers(insp)
    except sa.exc.NoInspectionAvailable:
        return get_mapper(expr).all_orm_descriptors
    attrs = dict(get_mapper(expr).all_orm_descriptors)
    for submapper in polymorphic_mappers:
        for key, descriptor in submapper.all_orm_descriptors.items():
            if key not in attrs:
                attrs[key] = descriptor
    return attrs


class QuerySorterException(Exception):
    pass


class QuerySorter:
    def __init__(self, silent=True, separator="-"):
        self.separator = separator
        self.silent = silent

    def assign_order_by(self, entity, attr, func):
        expr = get_query_descriptor(self.query, entity, attr)
        if expr is not None:
            return self.query.order_by(func(expr))
        if not self.silent:
            raise QuerySorterException("Could not sort query with expression '%s'" % attr)
        return self.query

    def parse_sort_arg(self, arg):
        if arg[0] == self.separator:
            func = desc
            arg = arg[1:]
        else:
            func = asc
        parts = arg.split(self.separator)
        return {
            "entity": parts[0] if len(parts) > 1 else None,
            "attr": parts[1] if len(parts) > 1 else arg,
            "func": func,
        }

    def __call__(self, query, *args):
        self.query = query
        for sort in args:
            if not sort:
                continue
            self.query = self.assign_order_by(**self.parse_sort_arg(sort))
        return self.query


def sort_query(query, *args, **kwargs):
    """
    Applies an sql ORDER BY for given query. This function can be easily used
    with user-defined sorting.
    The examples use the following model definition:
    ::
        import sqlalchemy as sa
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.declarative import declarative_base
        from sqlalchemy_utils import sort_query
        engine = create_engine(
            'sqlite:///'
        )
        Base = declarative_base()
        Session = sessionmaker(bind=engine)
        session = Session()
        class Category(Base):
            __tablename__ = 'category'
            id = sa.Column(sa.Integer, primary_key=True)
            name = sa.Column(sa.Unicode(255))
        class Article(Base):
            __tablename__ = 'article'
            id = sa.Column(sa.Integer, primary_key=True)
            name = sa.Column(sa.Unicode(255))
            category_id = sa.Column(sa.Integer, sa.ForeignKey(Category.id))
            category = sa.orm.relationship(
                Category, primaryjoin=category_id == Category.id
            )
    1. Applying simple ascending sort
    ::
        query = session.query(Article)
        query = sort_query(query, 'name')
    2. Applying descending sort
    ::
        query = sort_query(query, '-name')
    3. Applying sort to custom calculated label
    ::
        query = session.query(
            Category, sa.func.count(Article.id).label('articles')
        )
        query = sort_query(query, 'articles')
    4. Applying sort to joined table column
    ::
        query = session.query(Article).join(Article.category)
        query = sort_query(query, 'category-name')
    :param query:
        query to be modified
    :param sort:
        string that defines the label or column to sort the query by
    :param silent:
        Whether or not to raise exceptions if unknown sort column
        is passed. By default this is `True` indicating that no errors should
        be raised for unknown columns.
    """
    return QuerySorter(**kwargs)(query, *args)
