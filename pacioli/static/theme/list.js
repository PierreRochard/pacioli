// Bootstrap 5 port of Flask-Admin 2.2.1's bs4_modal.js and bs4_filters.js,
// for Select2 4. Loaded after theme/form.js on list views.
(function ($) {
    'use strict';

    // Remote modals: load the create/edit/details form into the dialog.
    $('body').on('click', '[data-bs-toggle="modal"][href]', function () {
        $($(this).attr('data-bs-target') + ' .modal-content').load($(this).attr('href'));
    });

    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (element) {
        bootstrap.Tooltip.getOrCreateInstance(element);
    });

    var AdminFilters = function (element, filtersElement, filterGroups, activeFilters) {
        var $root = $(element);
        var $container = $('.filters', $root);
        var lastCount = 0;

        function showApply() {
            $('button', $root).removeClass('d-none');
        }

        function getCount(name) {
            var idx = name.indexOf('_');
            return idx === -1 ? 0 : parseInt(name.substr(3, idx - 3), 10);
        }

        function makeName(name) {
            var result = 'flt' + lastCount + '_' + name;
            lastCount += 1;
            return result;
        }

        function removeFilter(event) {
            event.preventDefault();
            $(this).closest('tr').remove();
            if ($('.filters tr').length === 0) {
                $('button', $root).addClass('d-none');
                $('.filters tbody').remove();
            } else {
                showApply();
            }
        }

        // Create the value input for a filter: tags, a choice list, or text.
        function createFilterInput($cell, filterValue, filter) {
            var $field;
            if (filter.type === 'select2-tags') {
                $field = $('<input type="hidden" class="filter-val form-control">')
                    .attr('name', makeName(filter.arg)).val(filterValue);
            } else if (filter.options) {
                $field = $('<select class="filter-val form-select">').attr('name', makeName(filter.arg));
                $(filter.options).each(function () {
                    $field.append($('<option>').val(this[0]).text(this[1])
                        .prop('selected', !!filterValue && filterValue == this[0]));
                });
            } else {
                $field = $('<input type="text" class="filter-val form-control">')
                    .attr('name', makeName(filter.arg)).val(filterValue);
            }
            $cell.replaceWith($('<td>').append($field));
            $field.on('input change', showApply);
            return $field;
        }

        function styleFilterInput(filter, $field) {
            if (filter.type) {
                if (filter.type === 'datepicker' || filter.type === 'daterangepicker') {
                    $field.attr('data-date-format', 'YYYY-MM-DD');
                } else if (filter.type === 'datetimepicker' || filter.type === 'datetimerangepicker') {
                    $field.attr('data-date-format', 'YYYY-MM-DD HH:mm:ss');
                } else if (filter.type === 'timepicker' || filter.type === 'timerangepicker') {
                    $field.attr('data-date-format', 'HH:mm:ss');
                } else if (filter.type === 'select2-tags' && filter.options) {
                    $field.attr('data-tags', JSON.stringify(filter.options.map(function (option) {
                        return {id: option[0], text: option[1]};
                    })));
                }
                faForm.applyStyle($field, filter.type);
            } else if (filter.options) {
                faForm.applyStyle($field, 'select2');
            }
            return $field;
        }

        function addFilter(name, subfilters, selectedIndex, filterValue) {
            var $row = $('<tr>').appendTo($container);
            $row.append($('<td>').append(
                $('<a href="#" class="btn btn-secondary remove-filter">')
                    .append($('<i class="bi bi-x-lg" aria-hidden="true">'))
                    .append(' ').append(document.createTextNode(name))
                    .on('click', removeFilter)));

            // Operation (equals, not equals, ...)
            var $select = $('<select class="filter-op form-select">');
            var selection = 0;
            $.each(subfilters, function (index, subfilter) {
                var selected = this.index == selectedIndex;
                if (selected) {
                    selection = index;
                }
                $select.append($('<option>').attr('value', subfilter.arg)
                    .prop('selected', selected).text(subfilter.operation));
            });
            $row.append($('<td>').append($select));
            $select.select2({theme: 'bootstrap-5', width: 'resolve', minimumResultsForSearch: Infinity})
                .on('change', function () {
                    var chosen = subfilters[$select.prop('selectedIndex')];
                    styleFilterInput(chosen, createFilterInput($row.find('td').last(), null, chosen));
                    showApply();
                });

            var filter = subfilters[selection];
            var $field = createFilterInput($('<td>').appendTo($row), filterValue, filter);
            $field.trigger('focus');
            return styleFilterInput(filter, $field);
        }

        $('a.filter', filtersElement).on('click', function (event) {
            event.preventDefault();
            var name = $(this).text().trim();
            addFilter(name, filterGroups[name], false, null);
            showApply();
        });

        $.each(activeFilters, function (index, activeFilter) {
            addFilter(activeFilter[1], filterGroups[activeFilter[1]], activeFilter[0], activeFilter[2]);
        });

        $('.filter-val', $root).each(function () {
            var count = getCount($(this).attr('name') || '');
            if (count > lastCount) {
                lastCount = count;
            }
        });
        lastCount += 1;
    };

    $(function () {
        if ($('#filter-groups-data').length === 1) {
            new AdminFilters('#filter_form', '.field-filters',
                             JSON.parse($('#filter-groups-data').text()),
                             JSON.parse($('#active-filters-data').text()));
        }
    });
})(jQuery);
