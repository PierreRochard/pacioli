// Bootstrap 5 port of Flask-Admin 2.2.1's admin/js/form.js.
//
// Flask-Admin's widgets still render markup for Select2 3, daterangepicker 1.3
// and x-editable. This port drives Select2 4, daterangepicker 3 and a small
// inline editor from the same data-role attributes, so the Python widgets and
// the form data the server receives are unchanged. Map widgets are not
// supported.
(function () {
    'use strict';

    var AJAX_PAGE_SIZE = 10;
    var RANGE_SEPARATOR = ' to ';

    function select2Options($el, extra) {
        var options = $.extend({theme: 'bootstrap-5', width: '100%'}, extra);
        var $modal = $el.closest('.modal');
        if ($modal.length) {
            options.dropdownParent = $modal;  // keeps the search box focusable
        }
        if ($el.closest('.filters').length) {
            options.width = 'resolve';
        }
        return options;
    }

    // A hidden input keeps the field's name and value; Select2 needs a <select>.
    function companionSelect($input, multiple) {
        var $select = $('<select>').attr('aria-label', $input.attr('data-placeholder') || $input.attr('name'));
        if (multiple) {
            $select.attr('multiple', 'multiple');
        }
        $input.addClass('d-none').after($select);
        return $select;
    }

    function processAjaxWidget($input) {
        var multiple = $input.attr('data-multiple') === '1';
        var separator = $input.attr('data-separator') || ',';
        var $select = companionSelect($input, multiple);
        var current = $input.attr('data-json') ? JSON.parse($input.attr('data-json')) : null;
        var selected = current ? (multiple ? current : [current]) : [];

        if (!multiple) {
            $select.append(new Option('', '', false, false));
        }
        selected.forEach(function (pair) {
            $select.append(new Option(pair[1], pair[0], true, true));
        });

        $select.select2(select2Options($select, {
            placeholder: $input.attr('data-placeholder') || '',
            allowClear: !!$input.attr('data-allow-blank'),
            minimumInputLength: parseInt($input.attr('data-minimum-input-length') || '0', 10),
            ajax: {
                url: $input.attr('data-url'),
                dataType: 'json',
                delay: 250,
                data: function (params) {
                    var page = params.page || 1;
                    return {query: params.term || '', offset: (page - 1) * AJAX_PAGE_SIZE,
                            limit: AJAX_PAGE_SIZE};
                },
                processResults: function (data) {
                    var results = data.map(function (pair) {
                        return {id: pair[0], text: pair[1]};
                    });
                    return {results: results, pagination: {more: results.length === AJAX_PAGE_SIZE}};
                }
            }
        }));
        $select.on('change', function () {
            var value = $select.val();
            $input.val(multiple ? (value || []).join(separator) : (value || '')).trigger('change');
        });
    }

    // Free-form tags, submitted as one comma-separated value as before.
    function processTagsWidget($input) {
        var separator = ',';
        var choices = $input.attr('data-tags') ? JSON.parse($input.attr('data-tags')) : [];
        var allowDuplicates = $input.attr('data-allow-duplicate-tags') ?
            JSON.parse($input.attr('data-allow-duplicate-tags')) : false;
        var current = ($input.val() || '').split(separator).map(function (value) {
            return value.trim();
        }).filter(Boolean);
        var $select = companionSelect($input, true);

        choices.forEach(function (choice) {
            var id = typeof choice === 'object' ? String(choice.id) : String(choice);
            var text = typeof choice === 'object' ? choice.text : choice;
            $select.append(new Option(text, id, false, current.indexOf(id) >= 0));
        });
        current.forEach(function (value) {
            if (!$select.find('option').filter(function () { return this.value === value; }).length) {
                $select.append(new Option(value, value, true, true));
            }
        });

        var options = {tags: true, tokenSeparators: [separator],
                       language: {noResults: function () { return 'Enter comma separated values'; }}};
        if (allowDuplicates) {
            // A unique id per entry; the trailing space marks it for Flask-Admin.
            options.createTag = function (params) {
                var term = params.term.trim();
                return term ? {id: term + '#' + Date.now() + ' ', text: term} : null;
            };
        }
        $select.select2(select2Options($select, options));
        $select.on('change', function () {
            $input.val(($select.val() || []).join(separator)).trigger('change');
        });
    }

    function processSelect2($el) {
        var options = {minimumInputLength: parseInt($el.attr('data-minimum-input-length') || '0', 10)};
        if ($el.attr('data-allow-blank')) {
            options.allowClear = true;
            // Flask-Admin's blank choice is "__None"; clearing must select it.
            options.placeholder = $el.find('option[value="__None"]').length ?
                {id: '__None', text: ''} : '';
        }
        if ($el.attr('data-tags')) {
            options.tags = true;
            options.tokenSeparators = [','];
        }
        $el.select2(select2Options($el, options));
    }

    function processDatePicker($el, kind) {
        var single = ['datepicker', 'datetimepicker', 'timepicker'].indexOf(kind) >= 0;
        var withTime = kind.indexOf('time') >= 0;
        var format = $el.attr('data-date-format') ||
            (kind.indexOf('date') === 0 ? (withTime ? 'YYYY-MM-DD HH:mm:ss' : 'YYYY-MM-DD') : 'HH:mm:ss');
        var options = {
            singleDatePicker: single,
            showDropdowns: true,
            timePicker: withTime,
            timePicker24Hour: true,
            timePickerSeconds: format.indexOf('ss') >= 0,
            timePickerIncrement: 1,
            autoUpdateInput: false,  // never fill a blank field just by opening it
            locale: {format: format, separator: RANGE_SEPARATOR, cancelLabel: 'Clear'}
        };
        var value = $el.val();
        if (value) {
            var parts = single ? [value, value] : value.split(RANGE_SEPARATOR);
            var start = moment(parts[0], format);
            var end = moment(parts[1] || parts[0], format);
            if (start.isValid() && end.isValid()) {
                options.startDate = start;
                options.endDate = end;
            }
        } else if (withTime && single) {
            options.startDate = options.endDate = moment().seconds(0);
        }
        $el.attr('autocomplete', 'off').daterangepicker(options);
        if (kind.indexOf('time') === 0) {
            $el.data('daterangepicker').container.addClass('time-only');
        }
        $el.on('apply.daterangepicker', function (event, picker) {
            var text = picker.startDate.format(format);
            if (!single) {
                text += RANGE_SEPARATOR + picker.endDate.format(format);
            }
            $el.val(text).trigger('change');
        });
        $el.on('cancel.daterangepicker', function () {
            $el.val('').trigger('change');
        });
    }

    // Inline list editing (column_editable_list), replacing x-editable. The
    // request matches what Flask-Admin's ajax_update view expects.
    var INPUT_FORMATS = {date: 'YYYY-MM-DD', time: 'HH:mm:ss', 'datetime-local': 'YYYY-MM-DDTHH:mm:ss'};

    function inlineChoices($link) {
        var source = $link.attr('data-source');
        return source ? JSON.parse(source) : null;
    }

    function renderInlineValue($link, role, value, choices) {
        $link.empty();
        if (role === 'x-editable-boolean') {
            var yes = String(value) === '1';
            $link.append($('<i aria-hidden="true">').addClass(yes ? 'bi bi-check-circle' : 'bi bi-dash-circle'))
                 .append($('<span class="visually-hidden">').text(yes ? 'Yes' : 'No'));
            return;
        }
        var text = value === null || value === undefined ? '' : value;
        if (choices) {
            var values = [].concat(value).map(String);
            text = choices.filter(function (choice) {
                return values.indexOf(String(choice.value)) >= 0;
            }).map(function (choice) { return choice.text; }).join(', ');
        }
        if (String(text) === '') {
            $link.append($('<span class="inline-empty">').text('Empty'));
        } else {
            $link.text(text);
        }
    }

    function inlineControl($link, role) {
        var type = $link.attr('data-type');
        var value = $link.attr('data-value') || '';
        var choices = inlineChoices($link);
        var $control;
        if (choices) {
            var multiple = role === 'x-editable-select2-multiple';
            var selected = multiple ? value.split(',') : [value];
            $control = $('<select class="form-select form-select-sm">').prop('multiple', multiple);
            choices.forEach(function (choice) {
                $control.append(new Option(choice.text, choice.value, false,
                                           selected.indexOf(String(choice.value)) >= 0));
            });
        } else if (type === 'textarea') {
            $control = $('<textarea class="form-control form-control-sm">')
                .attr('rows', $link.attr('data-rows') || 5).val(value);
        } else if (type === 'number') {
            $control = $('<input type="number" class="form-control form-control-sm">')
                .attr('step', $link.attr('data-step') || '1').val(value);
        } else if (type === 'combodate') {
            var format = $link.attr('data-format');
            var inputType = format === 'YYYY-MM-DD' ? 'date' : (format === 'HH:mm:ss' ? 'time' : 'datetime-local');
            $control = $('<input class="form-control form-control-sm">').attr('type', inputType);
            if (inputType !== 'date') {
                $control.attr('step', 1);
            }
            var parsed = moment(value, format, true);
            if (parsed.isValid()) {
                $control.val(parsed.format(INPUT_FORMATS[inputType]));
            }
            $control.data('serverFormat', format).data('inputFormat', INPUT_FORMATS[inputType]);
        } else {
            $control = $('<input type="text" class="form-control form-control-sm">').val(value);
        }
        return $control.attr('aria-label', $link.attr('name'));
    }

    function controlValue($control) {
        var value = $control.val();
        var serverFormat = $control.data('serverFormat');
        if (serverFormat && value) {
            var inputFormat = $control.data('inputFormat');
            // Browsers omit seconds when they are zero.
            value = moment(value, [inputFormat, inputFormat.replace(':ss', '')], true).format(serverFormat);
        }
        return value;
    }

    function openInlineEditor($link, role) {
        if ($link.data('editing')) {
            return;
        }
        var choices = inlineChoices($link);
        var $control = inlineControl($link, role);
        var $save = $('<button type="submit" class="btn btn-primary" title="Save">' +
                      '<i class="bi bi-check-lg" aria-hidden="true"></i></button>');
        var $cancel = $('<button type="button" class="btn btn-outline-secondary" title="Cancel">' +
                        '<i class="bi bi-x-lg" aria-hidden="true"></i></button>');
        var $error = $('<div class="invalid-feedback d-block">');
        var $form = $('<form class="inline-edit-form">').append(
            $('<div class="input-group input-group-sm">').append($control, $save, $cancel), $error);

        function close() {
            $form.remove();
            $link.removeClass('d-none').data('editing', false).trigger('focus');
        }

        $link.addClass('d-none').data('editing', true).after($form);
        $control.trigger('focus');
        $cancel.on('click', close);
        $form.on('keydown', function (event) {
            if (event.key === 'Escape') {
                close();
            }
        });
        $form.on('submit', function (event) {
            event.preventDefault();
            var value = controlValue($control);
            var body = new URLSearchParams();
            body.append('list_form_pk', $link.attr('data-pk'));
            [].concat(value === null ? [] : value).forEach(function (item) {
                body.append($link.attr('name'), item);
            });
            if ($link.attr('data-csrf')) {
                body.append('csrf_token', $link.attr('data-csrf'));
            }
            $save.prop('disabled', true);
            $error.text('');
            fetch($link.attr('data-url'), {method: 'POST', body: body, credentials: 'same-origin'})
                .then(function (response) {
                    return response.text().then(function (text) {
                        return {ok: response.ok, text: text};
                    });
                })
                .then(function (result) {
                    if (!result.ok) {
                        $error.text(result.text || 'The change was not saved.');
                        $save.prop('disabled', false);
                        return;
                    }
                    $link.attr('data-value', [].concat(value === null ? [] : value).join(','));
                    renderInlineValue($link, role, value, choices);
                    close();
                })
                .catch(function () {
                    $error.text('The server could not be reached; the change was not saved.');
                    $save.prop('disabled', false);
                });
        });
    }

    function processInlineEditor($link, role) {
        $link.addClass('inline-editable').attr('role', 'button');
        if (role === 'x-editable-boolean' || !$link.text().trim()) {
            renderInlineValue($link, role, role === 'x-editable-boolean' ?
                $link.attr('data-value') : '', inlineChoices($link));
        }
        $link.on('click', function (event) {
            event.preventDefault();
            openInlineEditor($link, role);
        });
    }

    var AdminForm = function () {
        var fieldConverters = [];

        /**
         * Apply the widget for a data-role. Converters added with
         * addFieldConverter run first.
         */
        this.applyStyle = function ($el, name) {
            for (var i = 0; i < fieldConverters.length; i++) {
                if (fieldConverters[i]($el, name)) {
                    return true;
                }
            }
            switch (name) {
                case 'select2':
                    processSelect2($el);
                    return true;
                case 'select2-tags':
                    processTagsWidget($el);
                    return true;
                case 'select2-ajax':
                    processAjaxWidget($el);
                    return true;
                case 'datepicker':
                case 'daterangepicker':
                case 'datetimepicker':
                case 'datetimerangepicker':
                case 'timepicker':
                case 'timerangepicker':
                    processDatePicker($el, name);
                    return true;
                case 'x-editable':
                case 'x-editable-combodate':
                case 'x-editable-boolean':
                case 'x-editable-select2-multiple':
                    processInlineEditor($el, name);
                    return true;
                case 'leaflet':
                    console.error('Map widgets are not supported by the Pacioli admin theme');
                    return false;
            }
            return false;
        };

        /**
         * Add an inline form field from its template.
         */
        this.addInlineField = function (el, elID) {
            var $el = $(el).closest('.inline-field');
            var id = elID;
            var $parentForm = $el.parent().closest('.inline-field');
            if ($parentForm.hasClass('fresh')) {
                id = $parentForm.attr('id');
                if (elID) {
                    id += '-' + elID;
                }
            }

            var $fieldList = $el.find('> .inline-field-list');
            var maxId = 0;
            $fieldList.children('.inline-field').each(function () {
                var parts = $(this).attr('id').split('-');
                var next = parseInt(parts[parts.length - 1], 10) + 1;
                if (next > maxId) {
                    maxId = next;
                }
            });

            var prefix = id + '-' + maxId;
            var $template = $($el.find('> .inline-field-template').text());
            $template.attr('id', prefix).addClass('fresh');
            $('[name]', $template).each(function () {
                var $field = $(this);
                var fieldId = $field.attr('id');
                var fieldName = $field.attr('name');
                $field.attr('id', prefix + (fieldId !== '' ? '-' + fieldId : ''));
                $field.attr('name', prefix + (fieldName !== '' ? '-' + fieldName : ''));
            });
            $template.appendTo($fieldList);
            $template.find('input').first().trigger('focus');
            this.applyGlobalStyles($template);
        };

        /**
         * Apply widgets to every element with a data-role inside parent.
         */
        this.applyGlobalStyles = function (parent) {
            var self = this;
            $('input[data-role], select[data-role], textarea[data-role], a[data-role]', parent).each(function () {
                var $el = $(this);
                self.applyStyle($el, $el.attr('data-role'));
            });
        };

        this.addFieldConverter = function (converter) {
            fieldConverters.push(converter);
        };
    };

    $('body').on('click', '.inline-remove-field', function (event) {
        event.preventDefault();
        if (confirm($(this).attr('value'))) {
            $(this).closest('.inline-field').remove();
        }
    });

    window.faForm = new AdminForm();
    $(document).trigger('adminFormReady');

    $(function () {
        window.faForm.applyGlobalStyles(document);
    });
})();
